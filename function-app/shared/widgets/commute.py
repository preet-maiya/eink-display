"""Commute widget renderer — matches Route.dc.html mockup."""
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from shared.render_base import (
    W, H, PAD_X, PAD_Y, CONTENT_TOP, CONTENT_BOTTOM,
    draw_header, draw_footer, draw_thin_rule,
    font_display, font_mono, draw_eyebrow, to_bmp, now_local, text_w,
)


def _draw_traffic_bar(
    draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, severity: str
) -> None:
    """Traffic bar: empty=light, hatched=moderate, solid=heavy."""
    if severity == "heavy":
        draw.rectangle([(x, y), (x + w, y + h)], fill=0)
    elif severity == "moderate":
        draw.rectangle([(x, y), (x + w, y + h)], outline=0, width=2, fill=255)
        step = 6
        for offset in range(-h, w + h, step):
            x0, y0 = x + offset, y
            x1, y1 = x + offset + h, y + h
            x0c, x1c = max(x0, x), min(x1, x + w)
            y0c = y + max(0, x - x0)
            y1c = y + h - max(0, (x + w) - x1)
            if x0c < x1c and y0c < y1c:
                draw.line([(x0c, y0c), (x1c, y1c)], fill=0, width=1)
    else:  # light
        draw.rectangle([(x, y), (x + w, y + h)], outline=0, width=2, fill=255)


def render(
    data: Optional[dict],
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> bytes:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    now = now_local()
    # Direction: to work before noon, to home after 3pm
    hour = now.hour
    if 3 <= hour < 12:
        direction = "to Work"
    elif 15 <= hour < 22:
        direction = "to Home"
    else:
        direction = "Commute"

    time_str = now.strftime("%-I:%M %p")
    draw_header(draw, f"Commute — {direction}", time_str)

    if data is None:
        draw.text((W // 2, H // 2), "— no data yet —", font=font_mono(24), fill=0, anchor="mm")
        draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
        return to_bmp(img)

    routes = data.get("routes", [])

    content_h = CONTENT_BOTTOM - CONTENT_TOP
    n = min(len(routes), 3)
    if n == 0:
        draw.text((W // 2, H // 2), "— no routes —", font=font_mono(24), fill=0, anchor="mm")
        draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
        return to_bmp(img)

    card_gap = 12
    total_gap = card_gap * (n - 1)
    card_h = (content_h - total_gap) // n
    bar_w, bar_h = 120, 14

    for i, route in enumerate(routes[:3]):
        ry = CONTENT_TOP + i * (card_h + card_gap)
        border_w = 4 if i == 0 else 2
        draw.rectangle([(PAD_X, ry), (W - PAD_X, ry + card_h)], outline=0, width=border_w, fill=255)

        inner_x = PAD_X + 18
        inner_y = ry + 14

        # Left: label, route name, distance
        if i == 0:
            draw_eyebrow(draw, inner_x, inner_y, "Recommended", size=11)
        else:
            draw_eyebrow(draw, inner_x, inner_y, "Alternate", size=11)

        name_font = font_display(24 if i == 0 else 20)
        name = route.get("name", "—")
        # Leave room for the duration on the right; drop the second road if too wide
        time_font = font_display(40 if i == 0 else 34)
        dur = route.get("duration_min")
        dur_str = f"{dur} min" if dur else "—"
        name_max_w = W - PAD_X - 24 - text_w(draw, dur_str, time_font) - 16 - inner_x
        if text_w(draw, name, name_font) > name_max_w and " & " in name:
            name = name.split(" & ")[0]
        draw.text((inner_x, inner_y + 16), name, font=name_font, fill=0, anchor="lt")
        dist = route.get("distance_mi")
        dist_str = f"{dist:.1f} mi" if dist else "—"
        draw.text((inner_x, inner_y + 16 + (26 if i == 0 else 22) + 4), dist_str,
                  font=font_mono(13), fill=0, anchor="lt")

        # Right: time + traffic bar
        rx = W - PAD_X - 24
        draw.text((rx, inner_y + 4), dur_str, font=time_font, fill=0, anchor="rt")

        bar_x = rx - bar_w
        bar_y = inner_y + (26 if i == 0 else 22) + 18
        severity = route.get("traffic", "light")
        _draw_traffic_bar(draw, bar_x, bar_y, bar_w, bar_h, severity)

    draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
    return to_bmp(img)
