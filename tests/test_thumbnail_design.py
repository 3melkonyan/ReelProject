"""Lesson thumbnails — every group its own look,
every lesson its own frame and name, the Reel logo."""
import json
import subprocess

import pytest
from PIL import Image

from reel import thumbnail as th


def test_group_keeps_its_style_and_new_groups_get_unused_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "STYLES_FILE", tmp_path / "styles.json")
    assert [th.group_style(g) for g in ("A", "B", "A", "C")] == [0, 1, 0, 2]
    data = json.loads((tmp_path / "styles.json").read_text(encoding="utf-8"))
    del data["B"]                                   # a removed group frees its style
    (tmp_path / "styles.json").write_text(json.dumps(data), encoding="utf-8")
    assert (th.group_style("A"), th.group_style("C"), th.group_style("D")) == (0, 2, 1)


def test_all_styles_are_distinct():
    assert len(set(th.STYLES)) == len(th.STYLES) == 18


@pytest.mark.parametrize("title, name", [
    ("Դաս՝ Hash Table Implementation և Collision, Dynamic Array Resizing և Hashing",
     "Hash Table Implementation և Collision"),
    ("Closures և Context", "Closures և Context"),
    ("  Դաս՝  Context  ", "Context"),
])
def test_lesson_name_is_the_main_topic(title, name):
    assert th.lesson_name(title) == name


def frame():
    return Image.new("RGB", (1280, 720), (90, 90, 90))


@pytest.mark.parametrize("style", range(0, 18, 5))
def test_card_has_group_colour_logo_and_fits_long_text(style):
    name = "Շատ երկար անվանում " * 8 + "Supercalifragilisticexpialidocious" * 3
    img = th.render_card(frame(), "Group-A " * 10, name, style)
    primary = th.PALETTES[th.STYLES[style][0]][0]
    assert img.size == (1280, 720) and img.getpixel((10, 700)) == primary
    assert img.getpixel((th.M - 4, 92)) == th.WHITE                # the logo's white chip


def test_characters_missing_from_the_brand_font_fall_back():
    assert th.render_card(frame(), "Группа", "Урок Ж", 0).size == (1280, 720)


def test_make_thumbnail_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "STYLES_FILE", tmp_path / "styles.json")
    video, out = tmp_path / "v.mp4", tmp_path / "t.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    th.make_thumbnail(str(video), "Դաս՝ JavaScript this և OOP, Classes", str(out), group="Group-A")
    with Image.open(out) as img:
        assert img.size == (1280, 720) and img.format == "JPEG"
    assert json.loads((tmp_path / "styles.json").read_text(encoding="utf-8")) == {"Group-A": 0}
