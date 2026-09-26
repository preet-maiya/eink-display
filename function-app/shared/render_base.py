"""Base drawing utilities for all widgets."""
import io
import math
import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "America/Los_Angeles"))


def now_local() -> datetime:
    """Current time in the configured local timezone (default: America/Los_Angeles)."""
    return datetime.now(_local_tz())


def header_date() -> str:
    """Absolute date for widget headers, e.g. 'SAT · SEP 26'."""
    return now_local().strftime("%a · %b %-d").upper()


def format_updated(iso_ts: str) -> str:
    """Footer 'Updated' stamp: time only if today, else date + time."""
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(_local_tz())
    except ValueError:
        return iso_ts[:16]
    if dt.date() == now_local().date():
        return dt.strftime("%-I:%M %p")
    return dt.strftime("%b %-d, %-I:%M %p")

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
PAD_X, PAD_Y = 44, 36

# Vertical layout constants derived from CSS flex analysis
HEADER_TOP = PAD_Y          # 36
HEADER_H = 20
CONTENT_TOP = 74            # PAD_Y + HEADER_H + gap(18)
RULE_Y = 403                # thick separator rule y
FOOTER_Y = 424              # footer text top y
CONTENT_BOTTOM = RULE_Y - 14

FONTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fonts"))

_font_cache: dict = {}


def _load(filename: str, size: int) -> ImageFont.FreeTypeFont:
    key = (filename, size)
    if key not in _font_cache:
        path = os.path.join(FONTS_DIR, filename)
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except (IOError, OSError):
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]


def font_display(size: int) -> ImageFont.FreeTypeFont:
    return _load("SpaceGrotesk-Bold.ttf", size)


def font_mono(size: int) -> ImageFont.FreeTypeFont:
    return _load("IBMPlexMono-Regular.ttf", size)


def new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("L", (W, H), 255)
    return img, ImageDraw.Draw(img)


def text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def text_h(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def draw_eyebrow(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    size: int = 13,
    anchor: str = "lt",
) -> int:
    """Draw uppercase IBM Plex Mono text with 0.18em letter-spacing. Returns end x."""
    font = font_mono(size)
    text = text.upper()
    spacing = max(1, round(size * 0.18))
    if anchor in ("rt", "rm", "rb"):
        # Right-align: pre-measure total width
        total = sum(
            draw.textbbox((0, 0), c, font=font)[2] - draw.textbbox((0, 0), c, font=font)[0] + spacing
            for c in text
        ) - spacing
        x = x - total
    cx = x
    for char in text:
        draw.text((cx, y), char, font=font, fill=0, anchor="lt")
        bbox = draw.textbbox((0, 0), char, font=font)
        cx += (bbox[2] - bbox[0]) + spacing
    return cx


def draw_header(draw: ImageDraw.ImageDraw, left: str, right: str) -> None:
    draw_eyebrow(draw, PAD_X, HEADER_TOP, left)
    draw_eyebrow(draw, W - PAD_X, HEADER_TOP, right, anchor="rt")


def draw_thick_rule(draw: ImageDraw.ImageDraw, y: int = RULE_Y) -> None:
    draw.rectangle([(PAD_X, y), (W - PAD_X, y + 2)], fill=0)


def draw_thin_rule(
    draw: ImageDraw.ImageDraw, y: int, x0: int = PAD_X, x1: int = W - PAD_X
) -> None:
    draw.line([(x0, y), (x1, y)], fill=0, width=1)


def draw_vline(
    draw: ImageDraw.ImageDraw, x: int, y0: int = CONTENT_TOP, y1: int = CONTENT_BOTTOM
) -> None:
    draw.line([(x, y0), (x, y1)], fill=0, width=2)


def draw_battery(draw: ImageDraw.ImageDraw, x: int, y: int, pct: Optional[int]) -> int:
    """Draw battery icon. Returns right edge x."""
    body_w, body_h = 22, 12
    cap_w, cap_h = 3, 6
    draw.rectangle([(x, y), (x + body_w, y + body_h)], outline=0, width=2, fill=255)
    draw.rectangle(
        [(x + body_w + 1, y + (body_h - cap_h) // 2), (x + body_w + cap_w, y + (body_h + cap_h) // 2)],
        fill=0,
    )
    if pct is not None:
        fill_w = max(2, round((pct / 100.0) * (body_w - 4)))
        draw.rectangle([(x + 2, y + 2), (x + 2 + fill_w, y + body_h - 2)], fill=0)
    return x + body_w + cap_w


def draw_rotation_dots(
    draw: ImageDraw.ImageDraw, x: int, y: int, current: int, total: int
) -> None:
    """Draw rotation indicator dots, right-aligned starting at x."""
    dot_r = 4
    gap = 6
    cx = x
    for i in range(total):
        if i == current:
            draw.ellipse([(cx - dot_r, y - dot_r), (cx + dot_r, y + dot_r)], fill=0)
        else:
            draw.ellipse([(cx - dot_r, y - dot_r), (cx + dot_r, y + dot_r)], outline=0, width=2, fill=255)
        cx += dot_r * 2 + gap


def draw_footer(
    draw: ImageDraw.ImageDraw,
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> None:
    draw_thick_rule(draw)
    fy = FOOTER_Y
    font = font_mono(13)
    draw.text((PAD_X, fy), f"Updated {updated_at}", font=font, fill=0, anchor="lt")

    # Right side: dots then battery
    n = total_widgets
    dot_r = 4
    dot_gap = 6
    dots_w = n * dot_r * 2 + (n - 1) * dot_gap
    bat_w = 26  # body + cap
    gap_between = 14
    right_edge = W - PAD_X

    bat_x = right_edge - bat_w
    bat_y = fy + (text_h(draw, "0", font) - 12) // 2
    draw_battery(draw, bat_x, bat_y, battery_pct)

    pct_txt = f"{battery_pct}%" if battery_pct is not None else "--"
    pct_x = bat_x + bat_w + 4
    draw.text((pct_x, fy), pct_txt, font=font_mono(12), fill=0, anchor="lt")

    dots_x = bat_x - gap_between - dots_w
    dot_cy = fy + text_h(draw, "0", font) // 2
    draw_rotation_dots(draw, dots_x + dot_r, dot_cy, rotation_idx, n)


# ─── Weather icons ────────────────────────────────────────────────────────────

def _draw_sun(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    r = size * 0.22
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    ray_hw = size * 0.04
    ray_hh = size * 0.09
    ray_dist = size * 0.38
    for deg in range(0, 360, 45):
        rad = math.radians(deg)
        rx = cx + math.sin(rad) * ray_dist
        ry = cy - math.cos(rad) * ray_dist
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        corners = [(-ray_hw, -ray_hh), (ray_hw, -ray_hh), (ray_hw, ray_hh), (-ray_hw, ray_hh)]
        pts = [(rx + dx * cos_a - dy * sin_a, ry + dx * sin_a + dy * cos_a) for dx, dy in corners]
        draw.polygon(pts, fill=0)


def _draw_cloud_shape(
    draw: ImageDraw.ImageDraw,
    ox: float, oy: float, scale: float
) -> None:
    """Draw cloud shape. (ox,oy) = top-left of 100×100 viewBox at given scale."""
    def ellipse(cx, cy, rx, ry):
        draw.ellipse(
            [ox + (cx - rx) * scale, oy + (cy - ry) * scale,
             ox + (cx + rx) * scale, oy + (cy + ry) * scale],
            fill=0,
        )
    ellipse(52, 66, 36, 18)
    ellipse(34, 46, 17, 17)
    ellipse(58, 42, 21, 21)
    ellipse(76, 54, 14, 14)


def _draw_cloud(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    scale = size / 100.0
    ox = cx - 55 * scale
    oy = cy - 55 * scale
    _draw_cloud_shape(draw, ox, oy, scale)


def _draw_rain(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    scale = size / 100.0
    ox = cx - 55 * scale
    oy = cy - 60 * scale
    # cloud shifted up by 8px
    _draw_cloud_shape(draw, ox, oy - 8 * scale, scale * 0.85)
    # 3 rain strokes below cloud
    for i, rx in enumerate([28, 48, 68]):
        x0 = ox + rx * scale
        y0 = oy + 76 * scale
        angle = math.radians(20)
        dx, dy = math.sin(angle) * 20 * scale, math.cos(angle) * 20 * scale
        draw.line([(x0, y0), (x0 + dx, y0 + dy)], fill=0, width=max(1, round(size * 0.06)))


def _draw_partly_cloudy(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float
) -> None:
    # Small sun offset upper-left, cloud overlaid lower-right
    sun_scale = 0.55
    sun_cx = cx - size * 0.12
    sun_cy = cy - size * 0.12
    _draw_sun(draw, sun_cx, sun_cy, size * sun_scale)
    # Cloud over the lower-right
    cloud_scale = size / 100.0 * 0.9
    ox = cx - 45 * cloud_scale
    oy = cy - 40 * cloud_scale
    _draw_cloud_shape(draw, ox, oy, cloud_scale)


def _draw_snow(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    scale = size / 100.0
    ox = cx - 55 * scale
    oy = cy - 60 * scale
    _draw_cloud_shape(draw, ox, oy - 8 * scale, scale * 0.85)
    for i, rx in enumerate([28, 48, 68]):
        x0 = ox + rx * scale
        y0 = oy + 76 * scale
        r = max(2, round(size * 0.04))
        draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=0)


_ICON_DRAW = {
    "sun": _draw_sun,
    "cloud": _draw_cloud,
    "rain": _draw_rain,
    "partly_cloudy": _draw_partly_cloudy,
    "snow": _draw_snow,
}


def draw_weather_icon(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, condition: str
) -> None:
    fn = _ICON_DRAW.get(condition, _draw_sun)
    fn(draw, cx, cy, size)


def to_bmp(img: Image.Image) -> bytes:
    bmp = img.convert("1", dither=Image.Dither.NONE)
    buf = io.BytesIO()
    bmp.save(buf, format="BMP")
    return buf.getvalue()


def invert_bmp(bmp: bytes) -> bytes:
    """Swap black and white in a rendered BMP (dark mode)."""
    img = Image.open(io.BytesIO(bmp)).convert("L")
    return to_bmp(img.point(lambda p: 255 - p))
