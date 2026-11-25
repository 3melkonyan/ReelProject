"""Local ffmpeg thumbnail generation; retry and publication belong to pipeline."""
from __future__ import annotations

from reel.i18n import t as _t

import argparse
import fcntl
import io
import json
import math
import sqlite3
import subprocess
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from reel.publish.errors import PublishError


def _run(args: list[str]) -> bytes:
    return subprocess.run(args, check=True, capture_output=True, timeout=120).stdout


def _font(title: str, size: int):
    from PIL import ImageFont

    candidates = [
        (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
        (Path("/Library/Fonts/Arial Unicode.ttf"), 0),
        (Path("/System/Library/Fonts/NotoSansArmenian.ttc"), 6),
        (Path("/System/Library/Fonts/Supplemental/Mshtakan.ttc"), 0),
    ]
    # Prefer a single face covering BOTH scripts even for a single-script title.
    # Script-only macOS fonts must not silently become the primary family.
    for require_both in (True, False):
        probe = title + (" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ԱԲԳԴԵԶԷԸԹԺԻԼԽԾԿՀՁՂՃՄՅՆՇՈՉՊՋՌՍՎՏՐՑՒՓՔՕՖ աբգդեզէըթժիլխծկհձղճմյնշոչպջռսվտրցւփքօֆ" if require_both else "")
        for path, index in candidates:
            try:
                font = ImageFont.truetype(str(path), size, index=index)
                missing = font.getmask("\U0010ffff")
                signature = (missing.size, bytes(missing))
                if all((mask.size, bytes(mask)) != signature and mask.getbbox()
                       for char in set(probe) if not char.isspace()
                       for mask in [font.getmask(char)]):
                    return font  # The same font and point size draw the entire title.
            except OSError:
                continue
    raise PublishError(_t('thumbnail.font_missing'), False)


def _lines(draw, title: str, font, width: int) -> list[str] | None:
    lines: list[str] = []
    for word in title.split():
        if draw.textlength(word, font=font) > width:
            return None
        if lines and draw.textlength(lines[-1] + " " + word, font=font) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines if len(lines) <= 3 else None


# --- Thumbnail design ----------------------------------------------------------------
# Every group has its own look (a palette + a shape motif), every lesson its own
# frame and name.
ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / "assets"
BRAND_FONT = ASSETS / "NotoSansArmenian.ttf"      # OFL variable font: Latin + Armenian, Thin..Black
LOGO = ROOT / "reel" / "web" / "static" / "brand" / "reel-email.png"
STYLES_FILE = ROOT / "thumbnail_styles.json"       # group -> style index, kept once assigned
W, H, M = 1280, 720, 64
WHITE, INK = (255, 255, 255), (17, 17, 17)
MAGENTA, MINT, PINK, AMBER, DARK = (213, 1, 200), (113, 255, 234), (255, 61, 243), (252, 211, 77), (23, 23, 23)
PALETTES = [(MAGENTA, MINT), (MINT, MAGENTA), (PINK, AMBER), (AMBER, MAGENTA), (DARK, MINT), (DARK, PINK)]
STYLES = [(i % 6, (i + i // 6) % 3) for i in range(18)]    # (palette, motif): 18 distinct looks


@contextmanager
def styles_lock(path: Path | None = None):
    """One read-modify-write of the styles file at a time: the worker's first assignment
    (group_style) and the admin's choice on the web (reel.web.thumbnail_styles) run in
    different processes, and either could otherwise overwrite the other's change."""
    path = path or STYLES_FILE
    with open(path.with_name(path.name + ".lock"), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def group_style(group: str) -> int:
    """A group keeps its style once assigned (removing another group changes nothing);
    a new group uses the configured default, or the first unused style."""
    try:
        with styles_lock():
            return _group_style(group)
    except OSError:                                # no lock file possible: still a valid style
        return _group_style(group)


def _default_style() -> int | None:
    """Read the optional general default without initializing or changing the database."""
    path = STYLES_FILE.parent / "reel.db"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT value FROM account_settings WHERE key='default_thumbnail_style'").fetchone()
        value = int(row[0]) if row else -1
        return value if 0 <= value < len(STYLES) else None
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None


def _group_style(group: str) -> int:
    try:
        styles = json.loads(STYLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        styles = {}
    if not isinstance(styles, dict):
        styles = {}
    known = styles.get(group)
    if isinstance(known, int) and 0 <= known < len(STYLES):
        return known
    used = {v for v in styles.values() if isinstance(v, int)}
    style = _default_style()
    if style is None:
        style = next((i for i in range(len(STYLES)) if i not in used), len(used) % len(STYLES))
    styles[group] = style
    try:
        temporary = STYLES_FILE.with_name(STYLES_FILE.name + ".tmp")
        temporary.write_text(json.dumps(styles, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(STYLES_FILE)
    except OSError:
        pass                                       # still a valid style for this thumbnail
    return style


def lesson_name(title: str) -> str:
    """The main topic without the lesson prefix or extra topics after a comma."""
    text = " ".join(str(title or "").split())
    if text.startswith("Դաս՝"):
        text = text[len("Դաս՝"):].strip()
    return text.split(", ")[0].strip() or text


def _covers(font, text: str) -> bool:
    missing = font.getmask("\U0010ffff")
    signature = (missing.size, bytes(missing))
    return all((mask.size, bytes(mask)) != signature and mask.getbbox()
               for char in set(text) if not char.isspace() for mask in [font.getmask(char)])


def _brand_font(weight: str, size: int, text: str):
    """The bundled Noto Sans Armenian at a named weight; the older wide-coverage fonts
    (regular weight) only when the text has characters it lacks."""
    from PIL import ImageFont

    try:
        font = ImageFont.truetype(str(BRAND_FONT), size)
        for name in font.get_variation_names():
            if (name.decode() if isinstance(name, bytes) else name) == weight:
                font.set_variation_by_name(name)
                break
        if _covers(font, text):
            return font
    except OSError:
        pass
    return _font(text, size)


def _wrap(draw, text: str, font, width: int, max_lines: int) -> list[str] | None:
    lines: list[str] = []
    for word in text.split():
        if draw.textlength(word, font=font) > width:
            return None
        if lines and draw.textlength(lines[-1] + " " + word, font=font) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines if 0 < len(lines) <= max_lines else None


def _fit(draw, text: str, width: int, sizes, weight: str, max_lines: int):
    """The largest size that wraps the text into max_lines; at the smallest size the
    text is shortened word by word (then letter by letter) with "…"."""
    sizes = list(sizes)
    for size in sizes:
        font = _brand_font(weight, size, text)
        lines = _wrap(draw, text, font, width, max_lines)
        if lines:
            return font, lines
    font = _brand_font(weight, sizes[-1], text + "…")
    words = text.split()
    while len(words) > 1:
        words.pop()
        lines = _wrap(draw, " ".join(words) + "…", font, width, max_lines)
        if lines:
            return font, lines
    chars = text.strip()
    while chars and draw.textlength(chars + "…", font=font) > width:
        chars = chars[:-1]
    return font, [chars + "…"]


def _ink(bg) -> tuple:
    """Black or white text, whichever contrasts more with bg (WCAG relative luminance)."""
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * channel(bg[0]) + 0.7152 * channel(bg[1]) + 0.0722 * channel(bg[2])
    return INK if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else WHITE


def render_card(frame, group: str | None, name: str, style: int):
    """1280x720: the group's colour and motif, the lesson frame in a rounded window,
    the academy logo, the group pill and the lesson name."""
    from PIL import Image, ImageDraw

    palette, motif = STYLES[style % len(STYLES)]
    primary, secondary = PALETTES[palette]
    img = Image.new("RGBA", (W, H), primary + (255,))
    draw = ImageDraw.Draw(img)
    if motif == 0:
        draw.ellipse((W - 330, H - 250, W + 170, H + 250), fill=secondary)
    elif motif == 1:
        draw.ellipse((W - 420, -260, W + 60, 220), fill=secondary)
        draw.ellipse((W - 150, H - 130, W - 40, H - 20), fill=WHITE)
    else:
        draw.rounded_rectangle((W - 360, H - 230, W + 80, H + 120), 90, fill=secondary)
        draw.ellipse((M + 420, 40, M + 470, 90), fill=secondary)
    fw, fh = 560, 315
    fx, fy = W - M - fw, 112
    draw.rounded_rectangle((fx + 24, fy + 24, fx + fw + 24, fy + fh + 24), 40,
                           fill=secondary if primary == DARK else INK)
    window = frame.convert("RGB").resize((fw, fh), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (fw, fh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, fw - 1, fh - 1), 40, fill=255)
    window.putalpha(mask)
    img.alpha_composite(window, (fx, fy))
    if LOGO.exists():
        with Image.open(LOGO) as raw:
            logo = raw.convert("RGBA")
        logo = logo.resize((round(logo.width * 60 / logo.height), 60), Image.LANCZOS)
        draw.rounded_rectangle((M - 12, 44, M + logo.width + 12, 128), 20, fill=WHITE)
        img.alpha_composite(logo, (M, 56))
    column = fx - M - 40
    font, lines = _fit(draw, name, column, range(76, 34, -4), "Black", 3)
    ascent, descent = font.getmetrics()
    step = int((ascent + descent) * 0.96)
    top = H - 88 - step * len(lines)
    for index, line in enumerate(lines):
        draw.text((M, top + index * step), line, font=font, fill=_ink(primary))
    if group:
        pill, (label,) = _fit(draw, group, column - 44, range(32, 20, -2), "Bold", 1)
        ascent, descent = pill.getmetrics()
        y = top - (ascent + descent + 18) - 20
        box = (M, y, M + draw.textlength(label, font=pill) + 44, y + ascent + descent + 18)
        draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=WHITE)
        draw.text((M + 22, y + 9), label, font=pill, fill=INK)
    return img.convert("RGB")


def make_thumbnail(video_path: str, title: str, out_path: str,
                   at_sec: float | None = None, *, group: str | None = None, lesson_info: dict | None = None, variant: int = 0) -> str:
    """Select the sharpest of 20 frames and lay it out in the group's academy style
    with the group name and the lesson name (render_card)."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        if not isinstance(title, str) or not title.strip():
            raise PublishError(_t('thumbnail.empty_title'), False)
        duration = float(_run(["ffprobe", "-v", "error", "-show_entries",
                               "format=duration", "-of", "default=nw=1:nk=1",
                               str(video_path)]).decode().strip())
        if not math.isfinite(duration) or duration <= 0:
            raise PublishError(_t('thumbnail.invalid_duration'), False)
        if at_sec is not None and (not math.isfinite(at_sec) or not 0 <= at_sec < duration):
            raise PublishError(_t('thumbnail.invalid_frame_time'), False)
        times = [at_sec] if at_sec is not None else [duration * (.1 + .8 * i / 19) for i in range(20)]
        best, best_score = None, -1.0
        for timestamp in times:
            data = _run(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(video_path),
                         "-frames:v", "1", "-vf",
                         "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                         "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
            if not data:
                continue
            frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if frame is None:
                continue
            score = float(cv2.Laplacian(frame, cv2.CV_64F).var())
            if score > best_score:
                best, best_score = data, score
        if best is None:
            raise PublishError(_t('thumbnail.frame_unavailable'), False)
        with Image.open(io.BytesIO(best)) as source:
            frame = source.convert("RGB")
        rgb = (render_lesson_card(frame, lesson_info, variant) if lesson_info else
               render_card(frame, group, lesson_name(title), group_style(group) if group else 0))
        for quality in range(95, 9, -5):
            encoded = io.BytesIO()
            rgb.save(encoded, format="JPEG", quality=quality, optimize=True)
            if encoded.tell() < 2_000_000:
                Path(out_path).write_bytes(encoded.getvalue())
                return str(out_path)
        raise PublishError(_t('thumbnail.image_too_large'), False)
    except PublishError:
        raise
    except (ImportError, FileNotFoundError) as exc:
        raise PublishError(_t('thumbnail.missing_dependency', error=type(exc).__name__), False) from exc
    except Exception as exc:
        raise PublishError(_t('thumbnail.creation_failed', error=type(exc).__name__), False) from exc


def _smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="reel-thumbnail-") as directory:
        video = str(Path(directory) / "test.mp4")
        output = str(Path(directory) / "thumbnail.jpg")
        _run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
              "testsrc=size=640x360:rate=10:duration=10", "-c:v", "libx264",
              "-pix_fmt", "yuv420p", video])
        make_thumbnail(video, "Դաս 21. JavaScript this և OOP", output)
        from PIL import Image
        with Image.open(output) as result:
            assert result.size == (1280, 720) and result.format == "JPEG"
        assert Path(output).stat().st_size < 2_000_000
        make_thumbnail(video, "Դաս 21. JavaScript this և OOP", output, at_sec=5)
    print("OK: thumbnail (ffmpeg, Armenian glyphs, JPEG, size)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    _smoke()


def render_lesson_card(frame, info, variant=0):
    """Three Academy cards. Explicit course/group/lesson/subject, local OFL Armenian font."""
    from PIL import Image, ImageDraw, ImageOps, ImageFilter
    if variant not in (0,1,2):raise ValueError('thumbnail variant must be 0, 1 or 2')
    image=Image.new('RGB',(1280,720),DARK);d=ImageDraw.Draw(image)
    if variant==0:
        d.polygon([(800,0),(1280,0),(1280,720),(590,720)],fill=MAGENTA)
        crop=ImageOps.fit(frame,(530,298));image.paste(crop,(700,208))
        width=590
    elif variant==1:
        crop=ImageOps.fit(frame,(1280,720)).filter(ImageFilter.GaussianBlur(12)).convert('RGBA')
        crop.alpha_composite(Image.new('RGBA',crop.size,(0,0,0,205)))
        image=crop.convert('RGB');d=ImageDraw.Draw(image);width=1120
        d.rectangle((0,0,20,720),fill=MAGENTA)
    else:
        image=Image.new('RGB',(1280,720),(252,252,252));d=ImageDraw.Draw(image);width=1120
        d.rounded_rectangle((720,-90,1400,310),radius=90,fill=MINT)
        d.polygon([(0,620),(1280,510),(1280,720),(0,720)],fill=MAGENTA)
    ink=INK if variant==2 else WHITE
    def label(text,y,size,color=ink):
        text=str(text)
        minimum=48 if size>=70 else min(size,28)
        font,lines=_fit(d,text,width,range(size,minimum-1,-2),'Bold',2 if size==74 else 1)
        for i,line in enumerate(lines):d.text((64,y+i*(font.size+10)),line,font=font,fill=color)
    label(info.get('course','Reel'),48,38)
    label(info.get('group',''),112,32,MAGENTA if variant==2 else MINT)
    label(('Lesson ' if info.get('language') == 'en' else 'Դաս ')+str(info.get('lesson',1)),218,100)
    label(info.get('subject',''),370,74)
    label('Reel',638,28,WHITE if variant==2 else MINT)
    return image
