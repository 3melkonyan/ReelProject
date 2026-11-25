"""Render applies the board framing (scale -> rotate -> crop) on the CPU path."""
import subprocess

from reel.stages.render import framing_chain, render


def test_chain_for_lesson4_scales_first_then_levels_and_crops():
    c = framing_chain({"size": [3840, 2160], "rotate_deg": 0.369, "crop": [292, 234, 3398, 1912]},
                      {"width": 3840, "height": 2160, "fps": 24.0})
    assert "scale=w=2170:h=1220" in c and "rotate=0.006440" in c and c.endswith("crop=1920:1080:165:132[vo]")
    assert "fps=" not in c
    level = framing_chain({"size": [3840, 2160], "rotate_deg": 0.01, "crop": [0, 0, 3840, 2160]},
                          {"width": 3840, "height": 2160, "fps": 60.0})
    assert "rotate" not in level and level.startswith("[v]fps=30,")


def test_render_with_framing_gives_1080p(tmp_path):
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=60)
    fr = {"size": [1280, 720], "rotate_deg": 2.0, "crop": [100, 60, 1066, 600]}
    r = render(src, [(0.0, 1.0)], tmp_path / "o.mp4", framing=fr)
    assert r["path"] == "frame" and r["framing"] == fr
    wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", str(tmp_path / "o.mp4")], capture_output=True, text=True).stdout.strip()
    assert wh == "1920,1080"


def test_framed_iphone_hdr_comes_out_sdr_bt709(tmp_path):
    """Ա1: an iPhone HLG/BT.2020 lesson in a board-framed group (Group-A) must be converted
    to SDR BT.709 too, not only on the unframed phone path."""
    import pytest
    src = tmp_path / "hlg.mov"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-vf", "format=p010le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
                        "-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-pix_fmt", "p010le", "-b:v", "4M",
                        "-tag:v", "hvc1", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
                        "-colorspace", "bt2020nc", "-c:a", "aac", "-shortest", "-movflags", "+write_colr", str(src)],
                       check=True, timeout=120, capture_output=True)
    except subprocess.CalledProcessError:
        pytest.skip("HEVC VideoToolbox encoder is unavailable")
    fr = {"size": [1280, 720], "rotate_deg": 1.0, "crop": [80, 40, 1100, 640]}
    render(src, [(0.0, 0.8), (1.2, 2.0)], tmp_path / "o.mp4", framing=fr)
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=color_transfer,color_primaries,pix_fmt,width,height", "-of", "json",
                           str(tmp_path / "o.mp4")], capture_output=True, text=True).stdout
    import json
    v = json.loads(tags)["streams"][0]
    assert (v["width"], v["height"], v["pix_fmt"]) == (1920, 1080, "yuv420p")
    assert v.get("color_transfer") in (None, "unknown", "bt709"), v
    assert v.get("color_primaries") in (None, "unknown", "bt709"), v
