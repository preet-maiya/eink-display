"""Calendar widget renderer — matches Calendar.dc.html mockup."""
from datetime import datetime, date
from typing import Optional

from PIL import Image, ImageDraw

from shared.render_base import (
    W, H, PAD_X, PAD_Y, CONTENT_TOP, CONTENT_BOTTOM,
    draw_header, draw_footer, draw_vline, draw_thin_rule,
    font_display, font_mono, draw_eyebrow, text_h, to_bmp, now_local,
)

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def render(
    data: Optional[dict],
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> bytes:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    today = now_local()
    today_str = today.strftime("%a · %b %-d").upper()
    draw_header(draw, "Calendar", today_str)

    if data is None:
        draw.text((W // 2, H // 2), "— no data yet —", font=font_mono(24), fill=0, anchor="mm")
        draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
        return to_bmp(img)

    today_events = data.get("today", [])
    week_events = data.get("week", [])

    divider_x = PAD_X + 400

    # ── Left: Today's events ─────────────────────────────────────────────────
    draw_eyebrow(draw, PAD_X, CONTENT_TOP, "Today")

    left_content_top = CONTENT_TOP + 22
    row_h = max(36, (CONTENT_BOTTOM - left_content_top) // max(len(today_events), 4))
    row_h = min(row_h, 52)

    time_font = font_display(20)
    name_font = font_mono(20)
    time_col_w = 110

    for i, ev in enumerate(today_events[:6]):
        ey = left_content_top + i * row_h + (row_h - text_h(draw, "A", time_font)) // 2
        draw.text((PAD_X, ey), ev.get("time", ""), font=time_font, fill=0, anchor="lt")
        draw.text((PAD_X + time_col_w + 8, ey), ev.get("summary", ""), font=name_font, fill=0, anchor="lt")
        if i < len(today_events) - 1:
            rule_y = left_content_top + (i + 1) * row_h
            draw_thin_rule(draw, rule_y, PAD_X, divider_x - 20)

    if not today_events:
        draw.text((PAD_X, left_content_top + 20), "No events today", font=font_mono(18), fill=0, anchor="lt")

    # ── Vertical divider ─────────────────────────────────────────────────────
    draw_vline(draw, divider_x)

    # ── Right: Rest of week ───────────────────────────────────────────────────
    rx = divider_x + 28
    draw_eyebrow(draw, rx, CONTENT_TOP, "Rest of Week")

    week_top = CONTENT_TOP + 22
    week_font = font_mono(14)
    day_font = font_display(15)
    line_h = 28

    for i, entry in enumerate(week_events[:7]):
        wy = week_top + i * line_h
        draw.text((rx, wy), entry.get("day", ""), font=day_font, fill=0, anchor="lt")
        summary = entry.get("summary", "—")
        draw.text((rx + 46, wy), summary, font=week_font, fill=0, anchor="lt")

    draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
    return to_bmp(img)
