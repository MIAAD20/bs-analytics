"""
Loads the two variable fonts declared in theme.py and switches named weight
instances (Regular/Medium/Bold/Black...) on demand. Caches loaded fonts by
(family, weight, size) so repeated calls during one card render are cheap.

If a font file is ever missing or fails to load (e.g. it got deleted, or a
replacement font doesn't support variable weights), this falls back to
PIL's built-in default font rather than crashing card generation - a
missing font should never take down the whole feature.
"""
import logging
from functools import lru_cache

from PIL import ImageFont

from app.card import theme

logger = logging.getLogger("card.fonts")


@lru_cache(maxsize=64)
def get_font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    """
    family: "display" or "body" (keys in theme.FONTS)
    weight: a key in theme.FONT_WEIGHTS[family], e.g. "bold"
    size: pixel size
    """
    path = theme.FONTS[family]
    try:
        font = ImageFont.truetype(str(path), size)
        instance_name = theme.FONT_WEIGHTS.get(family, {}).get(weight)
        if instance_name:
            try:
                font.set_variation_by_name(instance_name)
            except Exception:
                logger.warning(f"Font '{family}' has no weight instance '{instance_name}', using default weight")
        return font
    except Exception:
        logger.exception(f"Failed to load font {path}, falling back to PIL default")
        return ImageFont.load_default(size=size)
