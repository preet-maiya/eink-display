"""Portfolio widget renderer — matches Portfolio.dc.html mockup."""
import math
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from shared.render_base import (
    header_date,
    W, H, PAD_X, PAD_Y, CONTENT_TOP, CONTENT_BOTTOM, RULE_Y,
    draw_header, draw_footer, draw_thick_rule, draw_thin_rule,
    font_display, font_mono, draw_eyebrow, text_w, to_bmp,
)


def _draw_triangle(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, size: int, up: bool
) -> None:
    h, w = size, size
    if up:
        pts = [(cx, cy - h // 2), (cx - w // 2, cy + h // 2), (cx + w // 2, cy + h // 2)]
    else:
        pts = [(cx, cy + h // 2), (cx - w // 2, cy - h // 2), (cx + w // 2, cy - h // 2)]
    draw.polygon(pts, fill=0)


def render(
    data: Optional[dict],
    updated_at: str,
    battery_pct: Optional[int],
    rotation_idx: int,
    total_widgets: int,
) -> bytes:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    today_str = header_date()
    draw_header(draw, "Portfolio", today_str)

    if data is None:
        draw.text((W // 2, H // 2), "— no data yet —", font=font_mono(24), fill=0, anchor="mm")
        draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
        return to_bmp(img)

    total_value = data.get("total_value", 0)
    total_change = data.get("total_change", 0)
    total_change_pct = data.get("total_change_pct", 0)
    holdings = data.get("holdings", [])

    # ── Summary row ───────────────────────────────────────────────────────────
    summary_top = CONTENT_TOP

    draw_eyebrow(draw, PAD_X, summary_top, "Total Value", size=12)
    total_font = font_display(56)
    total_str = f"${total_value:,.0f}"
    draw.text((PAD_X, summary_top + 16), total_str, font=total_font, fill=0, anchor="lt")

    # Change indicator (right side of summary)
    change_str = f"+${abs(total_change):,.0f}  ({abs(total_change_pct):.2f}%)"
    if total_change < 0:
        change_str = f"-${abs(total_change):,.0f}  ({abs(total_change_pct):.2f}%)"
    change_font = font_mono(18)
    change_x = W - PAD_X
    change_y = summary_top + 48
    tw = text_w(draw, change_str, change_font)
    tri_size = 16
    tri_cx = change_x - tw - 10
    tri_cy = change_y + 9
    _draw_triangle(draw, tri_cx, tri_cy, tri_size, up=(total_change >= 0))
    draw.text((change_x, change_y), change_str, font=change_font, fill=0, anchor="rt")

    # ── Thick rule ────────────────────────────────────────────────────────────
    table_rule_y = summary_top + 82
    draw.rectangle([(PAD_X, table_rule_y), (W - PAD_X, table_rule_y + 2)], fill=0)

    # ── Table ────────────────────────────────────────────────────────────────
    col1_x = PAD_X               # ticker
    col2_x = PAD_X + 220         # price (right-aligned)
    col3_x = W - PAD_X           # change (right-aligned)

    # Column headers
    hdr_y = table_rule_y + 8
    hdr_font = font_mono(12)
    draw_eyebrow(draw, col1_x, hdr_y, "Ticker", size=12)
    draw_eyebrow(draw, col2_x, hdr_y, "Price", size=12, anchor="rt")
    draw_eyebrow(draw, col3_x, hdr_y, "Change", size=12, anchor="rt")

    row_top = hdr_y + 22
    draw_thin_rule(draw, row_top - 2)

    row_font_ticker = font_display(18)
    row_font_data = font_mono(17)
    tri_small = 11

    available_h = CONTENT_BOTTOM - row_top
    n = min(len(holdings), 6)
    row_h = available_h // max(n, 1)
    row_h = min(row_h, 44)

    for i, holding in enumerate(holdings[:6]):
        ry = row_top + i * row_h
        row_mid = ry + row_h // 2

        draw.text((col1_x, row_mid), holding.get("ticker", ""), font=row_font_ticker, fill=0, anchor="lm")
        price = holding.get("price")
        price_str = f"${price:,.2f}" if price else "—"
        draw.text((col2_x, row_mid), price_str, font=row_font_data, fill=0, anchor="rm")

        pct = holding.get("change_pct", 0)
        pct_str = f"{abs(pct):.1f}%"
        up = pct >= 0
        tw_p = text_w(draw, pct_str, row_font_data)
        tri_cx2 = col3_x - tw_p - 8
        _draw_triangle(draw, tri_cx2, row_mid, tri_small, up=up)
        draw.text((col3_x, row_mid), pct_str, font=row_font_data, fill=0, anchor="rm")

        if i < n - 1:
            draw_thin_rule(draw, ry + row_h - 1)

    draw_footer(draw, updated_at, battery_pct, rotation_idx, total_widgets)
    return to_bmp(img)
