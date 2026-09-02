"""
Small flat vector icons for stat badges (trophy, wins, win-rate, streak,
favorite, global rank...), drawn with plain Pillow shapes. Deliberately NOT
using emoji glyphs: color-emoji rendering is fragile across fonts/servers,
so these are simple geometric icons instead - and they match the flat
gaming-UI look better anyway.

Each function draws centered inside a `size x size` box at (cx, cy) using
`color`. To add a new badge icon, add one function here following the same
signature and register it in ICONS at the bottom - nothing else needs to
change.
"""
import math

from PIL import ImageDraw


def trophy(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    w, h = size, size
    top = cy - h / 2
    cup_w = w * 0.62
    cup_top = top + h * 0.06
    cup_bottom = top + h * 0.55
    cup_left = cx - cup_w / 2
    cup_right = cx + cup_w / 2

    # cup body (trapezoid narrowing toward the base)
    draw.polygon(
        [
            (cup_left, cup_top), (cup_right, cup_top),
            (cx + cup_w * 0.28, cup_bottom), (cx - cup_w * 0.28, cup_bottom),
        ],
        fill=color,
    )
    # handles
    handle_r = w * 0.16
    lw = max(2, round(size * 0.06))
    draw.arc([cup_left - handle_r * 1.3, cup_top, cup_left + handle_r * 0.5, cup_top + handle_r * 2],
              start=90, end=270, fill=color, width=lw)
    draw.arc([cup_right - handle_r * 0.5, cup_top, cup_right + handle_r * 1.3, cup_top + handle_r * 2],
              start=270, end=90, fill=color, width=lw)
    # stem + base
    stem_w = w * 0.14
    draw.rectangle([cx - stem_w / 2, cup_bottom, cx + stem_w / 2, cup_bottom + h * 0.16], fill=color)
    base_w = w * 0.4
    base_y = cup_bottom + h * 0.16
    draw.rounded_rectangle([cx - base_w / 2, base_y, cx + base_w / 2, base_y + h * 0.1],
                            radius=h * 0.03, fill=color)


def checkmark_badge(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    """Circle outline + checkmark - used for 'wins' / success stats."""
    r = size / 2
    lw = max(2, round(size * 0.09))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=lw)
    p1 = (cx - r * 0.45, cy + r * 0.02)
    p2 = (cx - r * 0.1, cy + r * 0.38)
    p3 = (cx + r * 0.5, cy - r * 0.35)
    draw.line([p1, p2, p3], fill=color, width=lw, joint="curve")


def target(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    """Bullseye - used for win-rate / accuracy stats."""
    r = size / 2
    lw = max(2, round(size * 0.07))
    for frac in (1.0, 0.62, 0.26):
        rr = r * frac
        if frac == 0.26:
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color)
        else:
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=color, width=lw)


def flame(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    """Used for streaks / 'main brawler' badges."""
    h = size
    w = size * 0.72
    top = cy - h / 2
    bottom = cy + h / 2
    pts = [
        (cx, top),
        (cx + w * 0.38, top + h * 0.42),
        (cx + w * 0.5, top + h * 0.66),
        (cx + w * 0.22, bottom),
        (cx - w * 0.22, bottom),
        (cx - w * 0.5, top + h * 0.66),
        (cx - w * 0.38, top + h * 0.42),
    ]
    draw.polygon(pts, fill=color)


def star(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    r_outer = size / 2
    r_inner = r_outer * 0.42
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))
    draw.polygon(pts, fill=color)


def globe(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    r = size / 2
    lw = max(2, round(size * 0.07))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=lw)
    draw.line([cx - r, cy, cx + r, cy], fill=color, width=lw)
    draw.ellipse([cx - r * 0.42, cy - r, cx + r * 0.42, cy + r], outline=color, width=max(1, lw - 1))


def chevron_up(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    """Used for 'highest trophies' / progress-up badges."""
    w, h = size * 0.7, size * 0.55
    lw = max(2, round(size * 0.14))
    draw.line([(cx - w / 2, cy + h / 2), (cx, cy - h / 2)], fill=color, width=lw, joint="curve")
    draw.line([(cx, cy - h / 2), (cx + w / 2, cy + h / 2)], fill=color, width=lw, joint="curve")


ICONS = {
    "trophy": trophy,
    "checkmark": checkmark_badge,
    "target": target,
    "flame": flame,
    "star": star,
    "globe": globe,
    "chevron_up": chevron_up,
}


def draw_icon(name: str, draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    fn = ICONS.get(name)
    if fn is None:
        # unknown icon name should never crash a card render - draw a
        # simple dot as a visible-but-harmless placeholder instead
        r = size * 0.15
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        return
    fn(draw, cx, cy, size, color)
