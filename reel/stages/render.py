"""Cut + two-pass EBU R128 loudnorm in a single video re-encode."""
from __future__ import annotations

import json
import re
import math
import subprocess
from reel.encoding import h264_encoder
from reel.progress import observed_run as _observed_run



from pathlib import Path


def observed_run(*args, **kwargs):
    return _observed_run(*args, run=subprocess.run, **kwargs)

TARGET = {"I": -16.0, "TP": -1.5, "LRA": 11.0}
# Phone footage (iPhone 4K / HDR HEVC 10-bit / 60 fps / rotated) is normalised to what every later
# stage was measured on: H.264 8-bit SDR, shorter side <= 1080, <= 30 fps. 1080p H.264 input keeps
# the old, tested path unchanged. Measured on a synthetic 4K60 HDR clip: ~240-340 input fps.
MAX_SIDE = 1080          # shorter side of the published video (4K -> 1920x1080, portrait -> 1080x1920)
MAX_FPS = 30.5           # a lecture needs no 60 fps; it would double the blur stage
_SDR = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def video_info(src: str | Path) -> dict:
    """v:0 as displayed: codec, pix_fmt, width/height AFTER rotation, rotation, HDR transfer, fps."""
    r = observed_run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,pix_fmt,width,height,color_transfer,avg_frame_rate,r_frame_rate"
                        ":stream_side_data=rotation,displaymatrix:stream_tags=rotate", "-of", "json", str(src)],
                       capture_output=True, text=True, check=True, timeout=60)
    s = (json.loads(r.stdout).get("streams") or [{}])[0]
    rotation = float((s.get("tags") or {}).get("rotate", 0) or 0)
    mirror = False
    for side in s.get("side_data_list") or []:
        if "rotation" in side:
            rotation = float(side["rotation"])
        rows = [ln.split(":", 1)[1].split() for ln in str(side.get("displaymatrix", "")).splitlines() if ":" in ln]
        if len(rows) >= 2 and len(rows[0]) >= 2 and len(rows[1]) >= 2:
            mirror = int(rows[0][0]) * int(rows[1][1]) - int(rows[0][1]) * int(rows[1][0]) < 0
    rotation = int(round(rotation / 90)) * 90 % 360
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    if rotation in (90, 270):
        w, h = h, w
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            num, den = str(s.get(key, "0/1")).split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except ValueError:
            fps = 0.0
        if 0 < fps < 1000:
            break
    return {"codec": s.get("codec_name"), "pix_fmt": s.get("pix_fmt"), "width": w, "height": h,
            "rotation": rotation, "hdr": s.get("color_transfer") in ("arib-std-b67", "smpte2084"),
            "fps": round(fps, 3), "mirror": mirror}


def web_ready(info: dict) -> bool:
    """Already what the pipeline was built and measured on: keep the old path (copy when uncut)."""
    return (info["codec"] == "h264" and info["pix_fmt"] in ("yuv420p", "yuvj420p") and info["rotation"] == 0
            and 0 < min(info["width"], info["height"]) <= MAX_SIDE and not info["hdr"] and info["fps"] <= MAX_FPS)


def target_size(info: dict) -> tuple[int, int]:
    """Display size with the shorter side capped at MAX_SIDE (never upscaled), both even."""
    w, h = info["width"], info["height"]
    k = min(1.0, MAX_SIDE / min(w, h)) if min(w, h) > 0 else 1.0
    return max(2, round(w * k / 2) * 2), max(2, round(h * k / 2) * 2)


def _trim_graph(keep: list[tuple[float, float]], video: bool, split: bool = False) -> tuple[str, str]:
    parts, labels = [], []
    n = len(keep)
    sp = split and n > 1
    if sp:
        if video:
            parts.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)))
        parts.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
    for i, (s, e) in enumerate(keep):
        if video:
            parts.append(f"{f'[vs{i}]' if sp else '[0:v]'}trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
            labels.append(f"[v{i}]")
        parts.append(f"{f'[as{i}]' if sp else '[0:a]'}atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
    n = len(keep)
    out = "".join(labels) + (f"concat=n={n}:v=1:a=1[v][a]" if video else f"concat=n={n}:v=0:a=1[a]")
    return ";".join(parts), out


def measure_loudness(src: str | Path, keep, *, processing=None, time_map=None) -> dict:
    """Pass 1: measure loudness of the kept audio only."""
    trims, concat = _trim_graph(keep, video=False)
    prefix = ""
    if processing:
        from reel.stages.processing import studio_filter, retime_graph
        prefix = studio_filter(processing['studio_audio'])
    if time_map:
        from reel.stages.processing import retime_graph
        trims, concat = retime_graph(time_map), ""
    graph = f"{trims};{concat};[a]{prefix}loudnorm=I={TARGET['I']}:TP={TARGET['TP']}:LRA={TARGET['LRA']}:print_format=json[o]"
    graph = graph.replace(";;", ";")
    r = observed_run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-filter_complex", graph,
                        "-map", "[o]", "-f", "null", "-"], capture_output=True, text=True, check=True)
    return json.loads(re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)[-1])


def framing_chain(framing: dict, info: dict) -> str:
    """(reel.board.plan): scale so the board crop becomes the output size, level the frame
    (rotate, clockwise degrees), crop. Scaling first keeps the rotation cheap: it runs at about
    the output size instead of 4K. Landscape only; the crop has the output's 16:9 aspect."""
    W, H = info["width"], info["height"]
    fw, fh = framing["size"]
    x, y, cw, ch = (v * W / fw for v in framing["crop"])
    ow, oh = framing.get("output_size", (1920, 1080))
    k = ow / cw
    def even(v):
        return max(2, int(round(v / 2)) * 2)
    sw, sh = even(W * k), even(H * k)
    cx = min(max(int(round(x * k)), 0), sw - ow)
    cy = min(max(int(round(y * k)), 0), sh - oh)
    pre = "fps=30," if info["fps"] > MAX_FPS else ""
    rot = (f",rotate={math.radians(framing['rotate_deg']):.6f}:ow=iw:oh=ih:c=black"
           if abs(framing["rotate_deg"]) >= 0.05 else "")
    # out_primaries/out_transfer: a real HLG/PQ -> SDR conversion (iPhone HDR); -color_trc alone is ignored
    return (f"[v]{pre}scale=w={sw}:h={sh}:out_color_matrix=bt709:out_range=tv"
            f":out_primaries=bt709:out_transfer=bt709,format=yuv420p{rot}"
            f",crop={ow}:{oh}:{cx}:{cy}[vo]")


def render(src: str | Path, keep, out: str | Path, *, bitrate: str = "4M", hwdecode: bool = False,
           framing: dict | None = None, processing=None, time_map=None) -> dict:
    """Pass 2. Nothing cut -> copy the video stream, normalise audio only (minutes -> seconds).
    Cuts -> trim/concat re-encode (hwdecode=True: media-engine decode; measured no faster). Any failure of a
    fast path falls back to the plain software path, so speed never costs correctness."""
    from reel.stages.transcribe import media_duration
    m = measure_loudness(src, keep, processing=processing, time_map=time_map) if processing or time_map else measure_loudness(src, keep)
    ln = (f"loudnorm=I={TARGET['I']}:TP={TARGET['TP']}:LRA={TARGET['LRA']}"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    if processing:
        from reel.stages.processing import studio_filter
        ln = studio_filter(processing['studio_audio']) + ln
    if time_map and not (framing and framing.get('version') == 2):
        info = video_info(src)
        w,h=info['width'],info['height']
        ow,oh=target_size(info)
        # Identity display geometry: temporal processing must not introduce framing.
        framing={'version':2,'size':[w,h],'output_size':[ow,oh],'transition_sec':0,
                 'timeline':[{'start':0,'end':keep[-1][1], 'mode':'none',
                              'matrix':[[ow/w,0,0],[0,oh/h,0],[0,0,1]]}]}
    if framing and framing.get("version") == 2:
        from reel.framing_render import render as stream_render
        info = video_info(src)
        return stream_render(src, keep, Path(out), framing, info, ln, m, bitrate=bitrate,
                             **({'processing':processing,'time_map':time_map} if processing or time_map else {}))
    out = Path(out)
    tmp = out.with_suffix(".part.mp4")
    head = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    tail = ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    trims, concat = _trim_graph(keep, video=True)
    info = video_info(src)
    attempts = []

    def norm(chain: str, tags: bool = True) -> list[str]:
        # Output colour tags make ffmpeg insert a software format filter, which cannot take
        # VideoToolbox frames (measured: "Impossible to convert ... videotoolbox_vld"); scale_vt
        # already marks its frames bt709, so the VT path goes without them.
        return ["-filter_complex", f"{trims};{concat};{chain};[a]{ln},aresample=48000[ao]",
                "-map", "[vo]", "-map", "[ao]", "-c:v", h264_encoder(), "-b:v", bitrate,
                *(_SDR if tags else [])]
    if framing:                  # board framing needs CPU filters (VT frames cannot rotate/crop)
        trims, concat = _trim_graph(keep, video=True, split=True)   # decode once, not per segment
        chain = framing_chain(framing, info)
        attempts.append(("reencode_hw/frame", ["-hwaccel", "videotoolbox", "-i", str(src), *norm(chain)]))
        attempts.append(("reencode/frame", ["-i", str(src), *norm(chain)]))
    elif web_ready(info):          # the old, measured path, unchanged
        reencode = ["-i", str(src), "-filter_complex", f"{trims};{concat};[a]{ln},aresample=48000[ao]",
                    "-map", "[v]", "-map", "[ao]", "-c:v", h264_encoder(), "-b:v", bitrate]
        total = media_duration(src)
        if len(keep) == 1 and keep[0][0] <= 0.05 and keep[0][1] >= total - 0.05:
            attempts.append(("copy", ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                                      "-af", f"{ln},aresample=48000"]))
        if hwdecode:
            attempts.append(("reencode_hw", ["-hwaccel", "videotoolbox", *reencode]))
        attempts.append(("reencode", reencode))
    else:                        # phone footage: -> H.264 8-bit SDR, shorter side <= 1080, <= 30 fps
        w, h = target_size(info)
        pre = "fps=30," if info["fps"] > MAX_FPS else ""
        trims, concat = _trim_graph(keep, video=True, split=True)

        sw = (f"[v]{pre}scale=w={w}:h={h}:out_color_matrix=bt709:out_range=tv"
              f":out_primaries=bt709:out_transfer=bt709,format=yuv420p[vo]")
        if not info["mirror"]:
            turn = {0: "", 90: "transpose_vt=dir=cclock,", 180: "transpose_vt=dir=reversal,",
                    270: "transpose_vt=dir=clock,"}[info["rotation"]]
            attempts.append(("reencode_hw/vt", ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld",
                                              "-display_rotation", "0",
                                              "-i", str(src), *norm(f"[v]{pre}{turn}scale_vt=w={w}:h={h}:color_matrix=bt709"
                                                                    f":color_primaries=bt709:color_transfer=bt709[vo]",
                                                                    tags=False)]))
        # Frames in memory: ffmpeg's autorotation applies (portrait stays portrait, 180 is turned back).
        attempts.append(("reencode_hw/sw", ["-hwaccel", "videotoolbox", "-i", str(src), *norm(sw)]))
        attempts.append(("reencode/sw", ["-i", str(src), *norm(sw)]))
    errors = []
    for k, (mode, args) in enumerate(attempts):
        try:
            observed_run(head + args + tail, total=sum(b-a for a,b in keep), check=True, capture_output=True)
            break
        except subprocess.CalledProcessError as e:
            tmp.unlink(missing_ok=True)
            errors.append(f"{mode}: {(e.stderr or b'').decode('utf-8', 'replace').strip()[-300:]}")
            if k == len(attempts) - 1:
                raise RuntimeError("render failed — " + " | ".join(errors)) from e
    tmp.replace(out)  # atomic: a crash never leaves a half file under the final name
    # mode keeps the contract values (copy | reencode_hw | reencode); path tells which chain ran.
    mode, _, path = mode.partition("/")
    return {"final": str(out), "loudness_in": float(m["input_i"]), "mode": mode,
            "path": path or ("normalize" if not web_ready(info) else "plain"),
            "source": {k: info[k] for k in ("codec", "width", "height", "rotation", "hdr", "fps")},
            **({"framing": framing} if framing else {})}


def measured_integrated(path: str | Path) -> float:
    r = observed_run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True, check=True)
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


def make_preview(src: str | Path, out: Path, height: int = 720, vbitrate: str = "1200k") -> dict:
    """Light H.264 copy for the web dashboard (~1.3 Mbit/s, smooth over a tunnel).
    Runs at low CPU priority; only ever made from the BLURRED video."""
    import os
    out = Path(out)
    part = out.with_suffix(".part.mp4")
    observed_run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-hwaccel", "videotoolbox", "-i", str(src), "-vf", f"scale=-2:{height}",
                    "-c:v", "h264_videotoolbox", "-b:v", vbitrate, "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", str(part)],
                   check=True, capture_output=True, preexec_fn=lambda: os.nice(10))
    part.replace(out)
    return {"path": str(out), "size_mb": round(out.stat().st_size / 1e6, 1)}
