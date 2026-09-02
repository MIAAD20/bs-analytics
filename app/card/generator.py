"""
Player Card generator: builds the final PNG from a PlayerCardData object
(see schema.py). No database or API dependency - it only receives plain
data, which keeps rendering stable independent of everything upstream.

All visual constants live in theme.py, the badge/stat-block selection in
layout.py, and icon shapes in icons.py; adding an entry to theme.CARD_SIZES
makes a new card size available automatically.
"""
from __future__ import annotations

import hashlib
import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from app.card import assets, icons, theme
from app.card.fonts import get_font
from app.card.layout import BADGE_DEFINITIONS, STAT_BLOCK_DEFINITIONS
from app.card.schema import PlayerCardData


def _stable_color(seed: str) -> tuple:
    """Deterministic fallback color from a name - same brawler/player always
    gets the same color, without relying on Python's randomized str hash()."""
    digest = hashlib.md5(seed.encode()).hexdigest()
    idx = int(digest[:8], 16) % len(theme.FALLBACK_ICON_PALETTE)
    return theme.FALLBACK_ICON_PALETTE[idx]


def _mask_circle(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


class PlayerCardGenerator:
    def __init__(self, variant: str = "telegram"):
        if variant not in theme.CARD_SIZES:
            raise ValueError(f"Unknown card variant '{variant}'. Options: {list(theme.CARD_SIZES)}")
        self.variant = variant
        self.width, self.height = theme.CARD_SIZES[variant]
        # Area-based scale (not just width-based): a wide-but-short card
        # like "desktop" would otherwise inherit oversized icons/fonts from
        # its large width alone and overflow its short height. Scaling by
        # area keeps element sizes proportionate to how much space the
        # card actually has.
        self.scale = ((self.width * self.height) / (theme.REFERENCE_WIDTH * theme.REFERENCE_HEIGHT)) ** 0.5
        self.img = Image.new("RGBA", (self.width, self.height))
        self.draw = ImageDraw.Draw(self.img)

    def s(self, px: float) -> int:
        """Scale a reference-size pixel value to this card's actual size."""
        return round(px * self.scale)

    def _composite(self, draw_fn):
        """
        Draws onto a transparent full-canvas layer and alpha-composites it
        onto the card. Required for any semi-transparent fill/outline -
        ImageDraw paints literal pixel values on an RGBA image rather than
        blending with what's underneath, so translucent panels would
        otherwise come out as solid/opaque. Fully-opaque draws (text,
        icons) don't need this and can use self.draw directly.
        """
        layer = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer))
        self.img.alpha_composite(layer)

    async def generate(self, data: PlayerCardData) -> bytes:
        self._draw_background()

        mode = theme.CARD_LAYOUT_MODE.get(self.variant, "stacked")
        if mode == "columns":
            await self._generate_columns(data)
        else:
            await self._generate_stacked(data)

        self._draw_watermark()

        buf = io.BytesIO()
        self.img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    async def _generate_stacked(self, data: PlayerCardData):
        """Single column, sections flow top to bottom. Used by portrait/square."""
        margin = self.s(theme.LAYOUT["margin"])
        gap = self.s(theme.LAYOUT["section_gap"])
        x0, width = margin, self.width - margin * 2
        y = margin

        y = await self._draw_header(data, y, x0, width)
        y += gap
        y = self._draw_stat_blocks(data, y, x0, width)
        y += gap
        if data.top_brawlers:
            y = await self._draw_brawler_grid(data, y, x0, width)
            y += gap
        self._draw_badges(data, y, x0, width)

    async def _generate_columns(self, data: PlayerCardData):
        """
        Left column: identity + headline numbers + badges.
        Right column: brawler grid (self-sizing to fit whatever width/
        height it's given - see _draw_brawler_grid).
        Used for wide-short variants where stacking everything in one
        column would run off the bottom of the card.
        """
        margin = self.s(theme.LAYOUT["margin"])
        gap = self.s(theme.LAYOUT["section_gap"])
        col_gap = self.s(48)
        total_w = self.width - margin * 2 - col_gap
        left_w = round(total_w * 0.56)
        right_w = total_w - left_w
        left_x0 = margin
        right_x0 = margin + left_w + col_gap

        y = margin
        y = await self._draw_header(data, y, left_x0, left_w)
        y += gap
        y = self._draw_stat_blocks(data, y, left_x0, left_w)
        y += gap
        self._draw_badges(data, y, left_x0, left_w)

        if data.top_brawlers:
            available_height = self.height - margin * 2
            await self._draw_brawler_grid(data, margin, right_x0, right_w, max_height=available_height)

    # Sections are self-contained - safe to reorder.
    def _draw_background(self):
        top, bottom = theme.COLORS["bg_top"], theme.COLORS["bg_bottom"]
        grad = Image.new("RGBA", (1, self.height))
        for y in range(self.height):
            t = y / max(self.height - 1, 1)
            grad.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
        self.img.paste(grad.resize((self.width, self.height)), (0, 0))

        glow = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gw, gh = self.width * 0.9, self.height * 0.32
        gx, gy = self.width * 0.5, self.height * 0.1
        gdraw.ellipse([gx - gw / 2, gy - gh / 2, gx + gw / 2, gy + gh / 2], fill=theme.COLORS["glow"])
        glow = glow.filter(ImageFilter.GaussianBlur(radius=self.s(70)))
        self.img.alpha_composite(glow)

    async def _draw_header(self, data: PlayerCardData, y: int, x0: int, width: int) -> int:
        icon_size = self.s(theme.LAYOUT["player_icon_size"])
        border_w = self.s(theme.LAYOUT["player_icon_border"])

        icon_img = await assets.get_player_icon(data.icon_id)
        self._paste_icon(icon_img, data.name, x0, y, icon_size, border_color=theme.COLORS["accent_gold"], border_width=border_w)

        text_x = x0 + icon_size + self.s(28)
        kicker_font = get_font("body", "semibold", self.s(22))
        self.draw.text((text_x, y), "PLAYER PROFILE", font=kicker_font, fill=theme.COLORS["text_muted"])

        name_font = get_font("display", "bold", self.s(52))
        name_y = y + self.s(34)
        self.draw.text((text_x, name_y), data.name, font=name_font, fill=theme.COLORS["text_primary"])

        tag_font = get_font("body", "regular", self.s(28))
        tag_y = name_y + self.s(62)
        tag_line = data.tag
        if data.club_name:
            tag_line += f"   ·   {data.club_name}"
        self.draw.text((text_x, tag_y), tag_line, font=tag_font, fill=theme.COLORS["text_secondary"])

        if data.exp_level is not None:
            self._draw_level_pill(data.exp_level, x0 + width)

        return y + icon_size

    def _draw_level_pill(self, level: int, x1: int):
        pill_font = get_font("body", "bold", self.s(24))
        text = f"LVL {level}"
        bbox = self.draw.textbbox((0, 0), text, font=pill_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = self.s(22), self.s(12)
        margin = self.s(theme.LAYOUT["margin"])
        y0 = margin
        x0 = x1 - tw - pad_x * 2
        y1 = y0 + th + pad_y * 2
        self._composite(lambda d: d.rounded_rectangle(
            [x0, y0, x1, y1], radius=(y1 - y0) / 2,
            fill=theme.COLORS["accent_cyan"][:3] + (40,),
            outline=theme.COLORS["accent_cyan"], width=max(1, self.s(2))))
        self.draw.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=pill_font,
                        fill=theme.COLORS["accent_cyan"], anchor="mm")

    def _draw_stat_blocks(self, data: PlayerCardData, y: int, x0: int, width: int) -> int:
        visible = [b for b in STAT_BLOCK_DEFINITIONS if getattr(data, b.key) is not None]
        if not visible:
            return y

        block_h = self.s(150)
        x1 = x0 + width
        y0, y1 = y, y + block_h
        self._composite(lambda d: d.rounded_rectangle(
            [x0, y0, x1, y1], radius=self.s(theme.LAYOUT["panel_radius"]),
            fill=theme.COLORS["panel"], outline=theme.COLORS["panel_border"], width=1))

        seg_w = (x1 - x0) / len(visible)
        icon_size = self.s(theme.LAYOUT["badge_icon_size"])
        value_font = get_font("display", "bold", self.s(40))
        label_font = get_font("body", "regular", self.s(22))

        for i, block in enumerate(visible):
            cx = x0 + seg_w * i + seg_w / 2
            icon_cy = y0 + self.s(38)
            icons.draw_icon(block.icon, self.draw, cx, icon_cy, icon_size, theme.COLORS["accent_gold"])

            value = getattr(data, block.key)
            value_text = block.format(value)
            self.draw.text((cx, y0 + self.s(88)), value_text, font=value_font,
                            fill=theme.COLORS["text_primary"], anchor="mm")
            self.draw.text((cx, y0 + self.s(122)), block.label, font=label_font,
                            fill=theme.COLORS["text_secondary"], anchor="mm")

            if i > 0:
                divider_x = x0 + seg_w * i
                self._composite(lambda d, dx=divider_x: d.line(
                    [(dx, y0 + self.s(20)), (dx, y1 - self.s(20))], fill=theme.COLORS["divider"], width=1))

        return y1

    async def _draw_brawler_grid(self, data: PlayerCardData, y: int, x0: int, width: int,
                                  max_height: Optional[int] = None) -> int:
        """
        Self-fitting: icon size is derived from the column width it's
        given (not a fixed theme size), and row count is capped to
        max_height if provided. This is what lets the same method serve a
        full-width portrait grid AND a narrow desktop side-column without
        ever overflowing - no per-variant tuning needed.
        """
        label_font = get_font("body", "semibold", self.s(24))
        self.draw.text((x0, y), "TOP BRAWLERS", font=label_font, fill=theme.COLORS["text_muted"])
        label_h = self.s(44)
        y += label_h

        cols = theme.LAYOUT["brawler_grid_columns"]
        gap = self.s(theme.LAYOUT["brawler_grid_gap"])

        cell_w = (width - gap * (cols - 1)) / cols
        icon_size = min(self.s(theme.LAYOUT["brawler_icon_size"]), round(cell_w * 0.82))
        icon_size = max(icon_size, self.s(44))  # never shrink below legible
        cell_h = icon_size + self.s(46)

        shown = data.top_brawlers[: theme.LAYOUT["max_brawlers_shown"]]
        if max_height is not None:
            available = max_height - label_h
            max_rows = max(1, int((available + gap) // (cell_h + gap)))
            shown = shown[: max_rows * cols]

        for i, b in enumerate(shown):
            row, col = divmod(i, cols)
            cell_x0 = x0 + col * (cell_w + gap)
            cell_y0 = y + row * (cell_h + gap)

            self._composite(lambda d, cx0=cell_x0, cy0=cell_y0: d.rounded_rectangle(
                [cx0, cy0, cx0 + cell_w, cy0 + cell_h],
                radius=self.s(18), fill=theme.COLORS["brawler_slot_bg"],
                outline=theme.COLORS["brawler_slot_border"], width=1))

            icon_cx = cell_x0 + cell_w / 2
            icon_cy = cell_y0 + self.s(16) + icon_size / 2
            icon_img = await assets.get_brawler_icon(b.brawler_id)
            self._paste_icon(icon_img, b.name, icon_cx - icon_size / 2, icon_cy - icon_size / 2, icon_size)

            trophy_font = get_font("body", "bold", self.s(24))
            trophy_y = icon_cy + icon_size / 2 + self.s(20)
            trophy_icon_size = self.s(18)
            text = f"{b.trophies:,}"
            bbox = self.draw.textbbox((0, 0), text, font=trophy_font)
            text_w = bbox[2] - bbox[0]
            total_w = trophy_icon_size + self.s(6) + text_w
            start_x = icon_cx - total_w / 2
            icons.draw_icon("trophy", self.draw, start_x + trophy_icon_size / 2, trophy_y, trophy_icon_size,
                             theme.COLORS["accent_gold"])
            self.draw.text((start_x + trophy_icon_size + self.s(6), trophy_y), text, font=trophy_font,
                            fill=theme.COLORS["text_primary"], anchor="lm")

        rows = -(-len(shown) // cols)  # ceil
        return y + rows * (cell_h + gap)

    def _draw_badges(self, data: PlayerCardData, y: int, x0: int, width: int) -> int:
        visible = [b for b in BADGE_DEFINITIONS if getattr(data, b.key) is not None]
        if not visible:
            return y

        cols = 2
        gap = self.s(20)
        cell_w = (width - gap * (cols - 1)) / cols
        cell_h = self.s(84)

        icon_bg_size = self.s(56)
        icon_size = self.s(30)
        label_font = get_font("body", "regular", self.s(20))
        value_font = get_font("body", "semibold", self.s(26))

        for i, badge in enumerate(visible):
            row, col = divmod(i, cols)
            cx0 = x0 + col * (cell_w + gap)
            cy0 = y + row * (cell_h + gap)

            self._composite(lambda d, bx0=cx0, by0=cy0: d.rounded_rectangle(
                [bx0, by0, bx0 + cell_w, by0 + cell_h],
                radius=self.s(18), fill=theme.COLORS["panel"], outline=theme.COLORS["panel_border"], width=1))

            icon_cx = cx0 + self.s(20) + icon_bg_size / 2
            icon_cy = cy0 + cell_h / 2
            self._composite(lambda d, icx=icon_cx, icy=icon_cy: d.ellipse(
                [icx - icon_bg_size / 2, icy - icon_bg_size / 2,
                 icx + icon_bg_size / 2, icy + icon_bg_size / 2],
                fill=theme.COLORS["accent_gold"][:3] + (35,)))
            icons.draw_icon(badge.icon, self.draw, icon_cx, icon_cy, icon_size, theme.COLORS["accent_gold"])

            text_x = icon_cx + icon_bg_size / 2 + self.s(18)
            value = getattr(data, badge.key)
            self.draw.text((text_x, icon_cy - self.s(16)), badge.label, font=label_font,
                            fill=theme.COLORS["text_muted"], anchor="lm")
            self.draw.text((text_x, icon_cy + self.s(14)), badge.format(value), font=value_font,
                            fill=theme.COLORS["text_primary"], anchor="lm")

        rows = -(-len(visible) // cols)
        return y + rows * (cell_h + gap)

    def _draw_watermark(self):
        margin = self.s(theme.LAYOUT["margin"])
        font = get_font("body", "regular", self.s(20))
        self.draw.text((self.width - margin, self.height - self.s(36)), "Brawl Stars Analytics",
                        font=font, fill=theme.COLORS["watermark"], anchor="rm")

    def _paste_icon(self, img: Optional[Image.Image], fallback_seed: str, x: float, y: float, size: int,
                     border_color: Optional[tuple] = None, border_width: int = 0):
        if img is not None:
            circular = _mask_circle(img, size)
        else:
            circular = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            cd = ImageDraw.Draw(circular)
            color = _stable_color(fallback_seed)
            cd.ellipse([0, 0, size, size], fill=color)
            initials = "".join(w[0] for w in fallback_seed.split()[:2]).upper() or "?"
            font = get_font("display", "bold", round(size * 0.36))
            cd.text((size / 2, size / 2), initials, font=font, fill=(255, 255, 255, 255), anchor="mm")

        self.img.alpha_composite(circular, (round(x), round(y)))

        if border_color and border_width:
            self.draw.ellipse([x, y, x + size, y + size], outline=border_color, width=border_width)


async def generate_player_card(data: PlayerCardData, variant: str = "telegram") -> bytes:
    """Convenience function - what callers (API endpoint, Telegram bot) use."""
    gen = PlayerCardGenerator(variant)
    return await gen.generate(data)
