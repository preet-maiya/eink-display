"""Meal plan + grocery widget renderer — matches Menu.dc.html mockup."""
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from shared.render_base import (
    W, H, PAD_X, PAD_Y, CONTENT_TOP, CONTENT_BOTTOM,
    draw_header, draw_footer, draw_vline,
    font_display, font_mono, draw_eyebrow, to_bmp, now_local,
)

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def render(
    data: Optional[dict],
    settings: dict,
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> bytes:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    today = now_local()
    today_str = today.strftime("%a · %b %-d").upper()
    draw_header(draw, "This Week's Menu", today_str)

    meal_plan = settings.get("meal_plan", {})
    today_key = DAY_KEYS[today.weekday()]

    divider_x = PAD_X + 430

    # ── Left: 7-day meal plan ─────────────────────────────────────────────────
    n_rows = 7
    available_h = CONTENT_BOTTOM - CONTENT_TOP
    row_h = available_h // n_rows
    row_h = min(row_h, 44)

    day_font = font_display(15)
    meal_font = font_mono(17)
    tag_font = font_mono(11)

    for i, (key, label) in enumerate(zip(DAY_KEYS, DAY_LABELS)):
        ry = CONTENT_TOP + i * row_h
        meal = meal_plan.get(key, {})
        dinner = meal.get("dinner", "—")
        is_today = key == today_key

        if is_today:
            draw.rectangle([(PAD_X, ry), (divider_x - 10, ry + row_h - 2)], outline=0, width=3)
            draw.text((PAD_X + 8, ry + (row_h - 15) // 2), label, font=day_font, fill=0, anchor="lt")
            draw.text((PAD_X + 56, ry + (row_h - 17) // 2), dinner, font=meal_font, fill=0, anchor="lt")
            draw_eyebrow(draw, divider_x - 60, ry + (row_h - 13) // 2, "Today", size=11)
        else:
            draw.text((PAD_X + 8, ry + (row_h - 15) // 2), label, font=day_font, fill=0, anchor="lt")
            draw.text((PAD_X + 56, ry + (row_h - 17) // 2), dinner, font=meal_font, fill=0, anchor="lt")

    # ── Vertical divider ─────────────────────────────────────────────────────
    draw_vline(draw, divider_x)

    # ── Right: Grocery list for tonight ──────────────────────────────────────
    rx = divider_x + 24
    draw_eyebrow(draw, rx, CONTENT_TOP, "Grocery — For Tonight")

    today_meal = meal_plan.get(today_key, {})
    grocery = today_meal.get("grocery", [])

    grocery_top = CONTENT_TOP + 22
    item_font = font_mono(16)
    item_h = 26
    box_size = 15

    for i, item in enumerate(grocery[:8]):
        iy = grocery_top + i * item_h
        # Checkbox outline
        draw.rectangle([(rx, iy), (rx + box_size, iy + box_size)], outline=0, width=2, fill=255)
        draw.text((rx + box_size + 10, iy), item, font=item_font, fill=0, anchor="lt")

    if not grocery:
        draw.text((rx, grocery_top + 20), "Nothing listed", font=item_font, fill=0, anchor="lt")

    draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
    return to_bmp(img)
