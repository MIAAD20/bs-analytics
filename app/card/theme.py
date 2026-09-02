"""
Card theme: every color, font, and size constant used by the Player Card
generator. Restyling the card requires changing only this file.
"""
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"

# COLORS - dark gaming aesthetic. RGBA tuples (alpha 255 = opaque).
COLORS = {
    # background gradient, top -> bottom
    "bg_top": (24, 16, 46, 255),
    "bg_bottom": (10, 8, 20, 255),
    # subtle glow blob behind the header
    "glow": (120, 80, 220, 60),

    # translucent panel behind stat blocks / brawler grid
    "panel": (255, 255, 255, 14),
    "panel_border": (255, 255, 255, 28),

    "divider": (255, 255, 255, 30),

    "text_primary": (255, 255, 255, 255),
    "text_secondary": (176, 172, 200, 255),
    "text_muted": (120, 116, 145, 255),

    "accent_gold": (255, 199, 44, 255),
    "accent_cyan": (86, 220, 255, 255),
    "accent_pink": (255, 92, 150, 255),
    "accent_green": (108, 224, 140, 255),
    "accent_red": (255, 105, 105, 255),

    "brawler_slot_bg": (255, 255, 255, 18),
    "brawler_slot_border": (255, 255, 255, 35),

    "watermark": (255, 255, 255, 60),
}

# Deterministic fallback palette for icons that fail to fetch (offline CDN,
# invalid brawler id, etc). Selected via a stable hash of the name, so the
# same brawler always receives the same fallback color across cards.
FALLBACK_ICON_PALETTE = [
    (255, 199, 44, 255), (86, 220, 255, 255), (255, 92, 150, 255),
    (108, 224, 140, 255), (154, 120, 255, 255), (255, 140, 66, 255),
]

# FONTS - variable TTFs with named weight instances; get_font() in fonts.py
# handles loading and weight switching.
FONTS = {
    "display": FONT_DIR / "Orbitron-Variable.ttf",   # headers, big numbers - sci-fi/gaming feel
    "body": FONT_DIR / "Rubik-Variable.ttf",          # labels, names, secondary text - readable
}

# Named weight instances available per family (must match the .ttf file).
FONT_WEIGHTS = {
    "display": {"regular": "Regular", "bold": "Bold", "black": "Black"},
    "body": {"regular": "Regular", "medium": "Medium", "semibold": "SemiBold", "bold": "Bold"},
}

# CARD SIZES - add a variant here and it becomes selectable via ?variant=name.
CARD_SIZES = {
    "telegram": (1080, 1120),   # portrait, fits Telegram photo preview well
    "square": (1080, 1080),     # social media square
    "desktop": (1600, 900),     # wide profile card
}

# "stacked" = single column, sections flow top to bottom (portrait/square).
# "columns" = left column (identity + numbers) / right column (brawlers),
# used for wide-short cards where a single column would run off the bottom.
# Add a new variant above and register its mode here.
CARD_LAYOUT_MODE = {
    "telegram": "stacked",
    "square": "stacked",
    "desktop": "columns",
}

# LAYOUT - spacing/radii/icon sizes in pixels at the "telegram" reference
# size; generator.py scales these proportionally for other sizes.
LAYOUT = {
    "margin": 64,
    "panel_radius": 28,
    "panel_padding": 32,
    "section_gap": 28,

    "player_icon_size": 148,
    "player_icon_border": 5,

    "brawler_icon_size": 108,
    "brawler_grid_columns": 4,
    "brawler_grid_gap": 24,
    "max_brawlers_shown": 8,

    "badge_icon_size": 40,

    "divider_thickness": 2,
}

# Reference size all LAYOUT pixel values above are tuned for. Other card
# sizes scale by area ratio (not just width) - see PlayerCardGenerator.scale
# - so a wide-short card doesn't get oversized icons/fonts just because
# it's wide.
REFERENCE_WIDTH, REFERENCE_HEIGHT = CARD_SIZES["telegram"]
