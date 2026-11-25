"""One video decode -> per-frame YUV warp -> ONE encode, with the existing kept/loudnorm audio.

Y/U/V planes stay 4:2:0 throughout; no RGB round trip or intermediate video encode.
Only the new version-2 framing plans use this path. Existing format fallbacks are unchanged.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from reel.encoding import h264_encoder
from reel.framing import matrix_at, source_time
from reel.progress import EncoderChannel


def _read(stream, buffer):
    view = memoryview(buffer)
    position = 0
    while position < len(buffer):
        count = stream.readinto(view[position:])
        if not count:
            if position:
                raise RuntimeError("Truncated decoded video frame")
            return False
        position += count
    return True


def _video_graph(keep, rate):
    graph = []
    labels = []
    if len(keep) > 1:
        graph.append("[0:v]split=" + str(len(keep)) + "".join(f"[in{i}]" for i in range(len(keep))))
    for i, (a, b) in enumerate(keep):
        graph.append(
            f"{'[in' + str(i) + ']' if len(keep) > 1 else '[0:v]'}trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]"
        )
        labels.append(f"[v{i}]")
    graph.append("".join(labels) + f"concat=n={len(keep)}:v=1:a=0[v]")
    graph.append(
        f"[v]fps=fps={rate}:start_time=0,scale=out_color_matrix=bt709:out_range=tv:out_primaries=bt709:out_transfer=bt709,format=yuv420p[raw]"
    )
    return ";".join(graph)


class PlaneWarp:
    def __init__(self, plan):
        self.plan = plan
        self.ow, self.oh = plan["output_size"]
        self.last = None
        self.transforms = None
        self.seconds = 0.0
        self.frames = 0

    def apply(self, buffer, width, height, t):
        matrix = matrix_at(self.plan, t)
        if self.last is None or not np.allclose(self.last, matrix, rtol=0, atol=1e-10):
            scale = np.diag([0.5, 0.5, 1.0])
            chroma = scale @ matrix @ np.diag([2.0, 2.0, 1.0])
            self.transforms = (matrix, chroma)
            self.last = matrix
        ysize = width * height
        uvsize = ysize // 4
        data = np.frombuffer(buffer, np.uint8)
        planes = (
            data[:ysize].reshape(height, width),
            data[ysize : ysize + uvsize].reshape(height // 2, width // 2),
            data[ysize + uvsize :].reshape(height // 2, width // 2),
        )
        output = []
        started = time.perf_counter()
        for i, plane in enumerate(planes):
            h = self.transforms[0 if i == 0 else 1]
            size = (self.ow, self.oh) if i == 0 else (self.ow // 2, self.oh // 2)
            # Geometry is contained; replicate only protects subpixel edge rounding.
            if np.allclose(h[2], [0, 0, 1], atol=1e-9):
                out = cv2.warpAffine(
                    plane, h[:2], size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
                )
            else:
                out = cv2.warpPerspective(
                    plane, h, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
                )
            output.append(out.tobytes())
        self.seconds += time.perf_counter() - started
        self.frames += 1
        return b"".join(output)


def _log(handle):
    handle.seek(0)
    return handle.read().decode("utf-8", "replace")[-1500:]


def render(
    src,
    keep,
    out,
    framing,
    info,
    normalizer,
    loudness,
    *,
    bitrate="4M",
    codec=None,
    processing=None,
    time_map=None,
):
    from reel.stages.render import _SDR, _trim_graph

    width, height = info["width"], info["height"]
    ow, oh = framing["output_size"]
    if width % 2 or height % 2:
        raise ValueError("YUV framing needs even display dimensions")
    rate = Fraction(str(min(30.0, info["fps"]))).limit_denominator(100000)
    rate_arg = f"{rate.numerator}/{rate.denominator}"
    duration = time_map[-1]["final_end"] if time_map else sum(b - a for a, b in keep)
    audio_graph, audio_concat = _trim_graph(keep, video=False)
    audio_graph = audio_graph.replace("[0:a]", "[1:a]")
    video_graph = _video_graph(keep, rate_arg)
    if time_map:
        from reel.stages.processing import retime_graph

        audio_graph = retime_graph(time_map, input_index=1)
        audio_concat = ""
        video_graph = (
            retime_graph(time_map, video=True, rate=rate_arg)
            + f";[v]fps={rate_arg},scale=out_color_matrix=bt709:out_range=tv:out_primaries=bt709:out_transfer=bt709,format=yuv420p[raw]"
        )
    graph = f"{audio_graph};{audio_concat};[a]{normalizer},aresample=48000[ao]".replace(";;", ";")
    enhancer = None
    if processing and processing["board_enhance"]["enabled"]:
        from reel.stages.board_enhance import BoardEnhancer

        enhancer = BoardEnhancer(framing, processing["board_enhance"])
    codec = codec or h264_encoder()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part.mp4")
    warp = PlaneWarp(framing)
    started = time.perf_counter()
    decoder = encoder = None
    try:
        with (
            tempfile.TemporaryFile() as decode_log,
            tempfile.TemporaryFile() as encode_log,
            EncoderChannel(duration) as observation,
        ):
            decoder = subprocess.Popen(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(src),
                    "-filter_complex",
                    video_graph,
                    "-map",
                    "[raw]",
                    "-an",
                    "-sn",
                    "-dn",
                    "-pix_fmt",
                    "yuv420p",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=decode_log,
                stdin=subprocess.DEVNULL,
            )
            command = [
                "ffmpeg",
                *observation.args,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pixel_format",
                "yuv420p",
                "-video_size",
                f"{ow}x{oh}",
                "-framerate",
                rate_arg,
                "-i",
                "pipe:0",
                "-vn",
                "-i",
                str(src),
                "-filter_complex",
                graph,
                "-map",
                "0:v:0",
                "-map",
                "[ao]",
                "-map_metadata",
                "-1",
                "-c:v",
                codec,
                "-b:v",
                bitrate,
                *_SDR,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-t",
                f"{duration:.6f}",
                "-movflags",
                "+faststart",
                str(part),
            ]
            if codec == "libx264":
                command[-1:-1] = ["-preset", "veryfast"]
            encoder = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log
            )
            buffer = bytearray(width * height * 3 // 2)
            index = 0
            try:
                while _read(decoder.stdout, buffer):
                    if time_map:
                        from reel.stages.processing import to_source

                        t = to_source(index / float(rate), time_map)
                    else:
                        t = source_time(index / float(rate), keep)
                    if enhancer:
                        y = np.frombuffer(buffer, np.uint8, count=width * height).reshape(
                            height, width
                        )
                        y[:] = enhancer.apply(y, t)
                        chroma = np.frombuffer(buffer, np.uint8, offset=width * height).reshape(
                            2, height // 2, width // 2
                        )
                        enhancer.chroma(chroma[0], chroma[1])
                    encoder.stdin.write(warp.apply(buffer, width, height, t))
                    index += 1
                    observation.poll()
            except BrokenPipeError:
                raise RuntimeError("Framing encoder failed: " + _log(encode_log)) from None
            decoder.stdout.close()
            encoder.stdin.close()
            dc = decoder.wait(timeout=120)
            ec = encoder.wait(timeout=120)
            if dc or ec or not index:
                raise RuntimeError(
                    f"Framing stream failed ({dc}/{ec}): "
                    + _log(decode_log)
                    + " "
                    + _log(encode_log)
                )
        part.replace(out)
        return {
            "final": str(out),
            "loudness_in": float(loudness["input_i"]),
            "mode": "reencode",
            "path": "frame",
            "source": {k: info[k] for k in ("codec", "width", "height", "rotation", "hdr", "fps")},
            "framing": framing,
            "framing_render": {
                "frames": index,
                "seconds": round(time.perf_counter() - started, 4),
                "board_enhance": (
                    {"frames": enhancer.frames, "skipped": enhancer.skipped} if enhancer else None
                ),
                "warp_seconds": round(warp.seconds, 4),
                "codec": codec,
                "video_encodes": 1,
            },
        }
    finally:
        for process in (decoder, encoder):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=30)
        part.unlink(missing_ok=True)
