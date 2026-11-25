"""A lesson recorded in parts -> one video file (stage 'join', runs before 'probe').

inbox/<group>/<folder>/ holds the parts of ONE lesson. They are ordered by natural name order
(pipeline.parts_of) and joined into work/<job>/source_joined.<ext>:
  1. stream copy -- lossless, about a minute -- when every part has the same codecs, size and rates;
  2. otherwise one re-encode to the first part's size and frame rate (VideoToolbox, then libx264).
The joined length must equal the sum of the parts (± JOIN_TOL_SEC per part), or the job stops.
The original parts stay untouched in archive/.
"""
from __future__ import annotations

import json
import subprocess
from reel.progress import observed_run as _observed_run



from pathlib import Path

from reel.pipeline import StopJob


def observed_run(*args, **kwargs):
    return _observed_run(*args, run=subprocess.run, **kwargs)

JOIN_TOL_SEC = 1.5      # per part: container/priming rounding of a stream-copy concat


def _probe(path: Path) -> dict | None:
    try:
        r = observed_run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,sample_rate,channels"
                            ":format=duration,bit_rate", "-of", "json", str(path)],
                           check=True, capture_output=True, text=True, timeout=120)
        j = json.loads(r.stdout)
        streams = j.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(j["format"]["duration"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        return None
    return {"duration": duration, "bit_rate": int(j["format"].get("bit_rate") or 0), "video": v, "audio": a,
            "sig": (v and (v.get("codec_name"), v.get("width"), v.get("height"), v.get("pix_fmt"),
                           v.get("r_frame_rate")),
                    a and (a.get("codec_name"), a.get("sample_rate"), a.get("channels")))}


def _concat_line(path: Path) -> str:
    return "file '" + str(path).replace("'", "'\\''") + "'\n"


def _attempts(parts: list[Path], info: list[dict], listfile: Path):
    """(mode, output extension, ffmpeg args without the output path), cheapest first."""
    head = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if len({i["sig"] for i in info}) == 1:
        yield "copy", parts[0].suffix.lower(), head + [
            "-f", "concat", "-safe", "0", "-i", str(listfile), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy"]
    v = info[0]["video"]
    w, h = int(v["width"]) // 2 * 2, int(v["height"]) // 2 * 2
    fps = v.get("r_frame_rate") if v.get("r_frame_rate") not in (None, "0/0", "0/1") else "25"
    n = len(parts)
    graph = ";".join(
        f"[{k}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={fps},format=yuv420p[v{k}];"
        f"[{k}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]" for k in range(n))
    graph += ";" + "".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    common = head + [x for p in parts for x in ("-i", str(p))] + [
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    bitrate = str(max(3_000_000, min(12_000_000, info[0]["bit_rate"] or 6_000_000)))
    yield "reencode_hw", ".mp4", common + ["-c:v", "h264_videotoolbox", "-b:v", bitrate]
    yield "reencode", ".mp4", common + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def join(parts: list[Path], workdir: Path) -> dict:
    info = []
    for p in parts:
        i = _probe(p)
        if i is None or not i["video"] or not i["audio"]:
            raise StopJob(f"part '{p.name}' is unreadable or has no video/audio track")
        info.append(i)
    expected = sum(i["duration"] for i in info)
    listfile = workdir / "join_list.txt"
    listfile.write_text("".join(_concat_line(p) for p in parts), encoding="utf-8")
    problems = []
    for mode, ext, args in _attempts(parts, info, listfile):
        tmp, final = workdir / f"source_joined.part{ext}", workdir / f"source_joined{ext}"
        try:
            observed_run(args + [str(tmp)], total=expected, check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError):
            problems.append(f"{mode}: ffmpeg failed")
            tmp.unlink(missing_ok=True)
            continue
        got = (_probe(tmp) or {}).get("duration")
        if got is None or abs(got - expected) > JOIN_TOL_SEC * len(parts):
            problems.append(f"{mode}: joined {got} s, parts {expected:.1f} s")
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(final)
        return {"file": str(final), "parts": len(parts), "names": [p.name for p in parts],
                "part_sec": [round(i["duration"], 2) for i in info], "joined_sec": round(got, 2), "mode": mode}
    raise StopJob(f"cannot join {len(parts)} parts: " + "; ".join(problems))
