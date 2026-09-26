"""Weather widget renderer."""
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from shared.render_base import (
    W, H, PAD_X, PAD_Y, CONTENT_TOP, CONTENT_BOTTOM,
    draw_header, draw_footer, draw_vline, draw_weather_icon,
    draw_thin_rule, draw_eyebrow,
    font_display, font_mono, text_w, text_h, to_bmp, now_local,
)

WMO_CONDITION = {
    0: ("Clear", "sun"), 1: ("Mostly Clear", "sun"), 2: ("Partly Cloudy", "partly_cloudy"),
    3: ("Overcast", "cloud"), 45: ("Foggy", "cloud"), 48: ("Foggy", "cloud"),
    51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Drizzle", "rain"),
    56: ("Freezing Drizzle", "rain"), 57: ("Freezing Drizzle", "rain"),
    61: ("Rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy Rain", "rain"),
    66: ("Freezing Rain", "rain"), 67: ("Freezing Rain", "rain"),
    71: ("Snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy Snow", "snow"),
    77: ("Snow Grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"),
    82: ("Heavy Showers", "rain"), 85: ("Snow Showers", "snow"), 86: ("Snow Showers", "snow"),
    95: ("Thunderstorm", "rain"), 96: ("Thunderstorm", "rain"), 99: ("Thunderstorm", "rain"),
}


def _wmo(code: int) -> tuple[str, str]:
    return WMO_CONDITION.get(code, ("—", "sun"))


def render(
    data: Optional[dict],
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> bytes:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    today_str = now_local().strftime("%a · %b %-d").upper()
    draw_header(draw, "Weather", today_str)

    if data is None:
        draw.text((W // 2, H // 2), "— no data yet —", font=font_mono(24), fill=0, anchor="mm")
        draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
        return to_bmp(img)

    cur = data.get("current", {})
    forecast = data.get("forecast", [])

    temp_f = cur.get("temp_f")
    code = cur.get("weathercode", 0)
    hi = cur.get("high_f")
    lo = cur.get("low_f")
    feels_like = cur.get("feels_like_f")
    wind_mph = cur.get("wind_mph")
    wind_dir = cur.get("wind_dir", "")
    humidity = cur.get("humidity_pct")
    uv_index = cur.get("uv_index")
    precip_today = cur.get("precip_today_pct")
    sunrise = cur.get("sunrise", "")
    sunset = cur.get("sunset", "")
    cond_text, icon_type = _wmo(code)

    DIVIDER_X = 415
    LEFT_TEXT_X = 192  # right of 120px icon + 28px gap

    # ── Left: weather icon ────────────────────────────────────────────────────
    icon_size = 120
    icon_cx = PAD_X + icon_size // 2 + 4   # ≈ 108
    # vertically align icon center with the temp+condition block
    icon_cy = CONTENT_TOP + 80
    draw_weather_icon(draw, icon_cx, icon_cy, icon_size, icon_type)

    # ── Temp ──────────────────────────────────────────────────────────────────
    temp_str = f"{round(temp_f)}°" if temp_f is not None else "—°"
    ty_temp = CONTENT_TOP - 4
    draw.text((LEFT_TEXT_X, ty_temp), temp_str, font=font_display(112), fill=0, anchor="lt")

    cond_y = ty_temp + 116
    draw.text((LEFT_TEXT_X, cond_y), cond_text, font=font_mono(17), fill=0, anchor="lt")

    hi_lo = f"H {round(hi)}°   L {round(lo)}°" if hi and lo else "—"
    draw.text((LEFT_TEXT_X, cond_y + 26), hi_lo, font=font_mono(14), fill=0, anchor="lt")

    # ── Detail rows ───────────────────────────────────────────────────────────
    detail_y = cond_y + 62
    draw_thin_rule(draw, detail_y - 8, x0=LEFT_TEXT_X, x1=DIVIDER_X - 16)

    mono13 = font_mono(13)
    col2_x = LEFT_TEXT_X + 118

    # Row 1: feels like | wind
    if feels_like is not None:
        draw.text((LEFT_TEXT_X, detail_y), f"Feels {round(feels_like)}°", font=mono13, fill=0, anchor="lt")
    if wind_mph is not None:
        wind_txt = f"Wind {round(wind_mph)} {wind_dir}".strip()
        draw.text((col2_x, detail_y), wind_txt, font=mono13, fill=0, anchor="lt")

    # Row 2: sunrise | sunset
    sun_y = detail_y + 24
    if sunrise:
        draw.text((LEFT_TEXT_X, sun_y), f"Rise {sunrise}", font=mono13, fill=0, anchor="lt")
    if sunset:
        draw.text((col2_x, sun_y), f"Set  {sunset}", font=mono13, fill=0, anchor="lt")

    # ── Vertical divider ──────────────────────────────────────────────────────
    draw_vline(draw, DIVIDER_X)

    # ── Right: 5-day forecast ─────────────────────────────────────────────────
    fc_start_x = DIVIDER_X + 28
    fc_area_w = W - PAD_X - fc_start_x
    n_fc = min(len(forecast), 5)
    FC_ICON = 44
    fc_bottom_y = CONTENT_TOP

    if n_fc:
        col_w = fc_area_w // n_fc
        for i, day in enumerate(forecast[:5]):
            col_cx = fc_start_x + i * col_w + col_w // 2
            col_y = CONTENT_TOP

            day_label = day.get("day", "")
            lbl_font = font_mono(12)
            draw_eyebrow(
                draw,
                col_cx - text_w(draw, day_label, lbl_font) // 2,
                col_y, day_label, size=12,
            )

            icon_top = col_y + 22
            draw_weather_icon(
                draw, col_cx, icon_top + FC_ICON // 2, FC_ICON,
                _wmo(day.get("weathercode", 0))[1],
            )

            fc_hi = day.get("high_f")
            fc_lo = day.get("low_f")
            fc_temp = f"{round(fc_hi)}/{round(fc_lo)}" if fc_hi and fc_lo else "—"
            temp_y = icon_top + FC_ICON + 10
            draw.text((col_cx, temp_y), fc_temp, font=font_mono(13), fill=0, anchor="mt")

            precip = day.get("precip_pct")
            if precip is not None:
                draw.text(
                    (col_cx, temp_y + 20), f"{precip}%",
                    font=font_mono(11), fill=0, anchor="mt",
                )

            fc_bottom_y = temp_y + 40

    # ── Right: today highlights (humidity / UV / rain) ────────────────────────
    hl_rule_y = fc_bottom_y + 18
    if hl_rule_y < CONTENT_BOTTOM - 70:
        draw_thin_rule(draw, hl_rule_y, x0=fc_start_x, x1=W - PAD_X)

        items = []
        if humidity is not None:
            items.append(("HUMIDITY", f"{round(humidity)}%"))
        if uv_index is not None:
            items.append(("UV INDEX", str(uv_index)))
        if precip_today is not None:
            items.append(("RAIN", f"{precip_today}%"))

        if items:
            slot_w = (W - PAD_X - fc_start_x) // len(items)
            lbl_y = hl_rule_y + 14
            val_y = lbl_y + 18
            for j, (label, val) in enumerate(items):
                cx = fc_start_x + j * slot_w + slot_w // 2
                lbl_font = font_mono(11)
                draw_eyebrow(
                    draw,
                    cx - text_w(draw, label, lbl_font) // 2,
                    lbl_y, label, size=11,
                )
                draw.text((cx, val_y), val, font=font_display(32), fill=0, anchor="mt")

    draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
    return to_bmp(img)
