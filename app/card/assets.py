"""
Fetches brawler/player icons from the CDN configured in config.py
(BRAWLER_ICON_URL_TEMPLATE / PLAYER_ICON_URL_TEMPLATE), caches them to disk
forever (icon IDs never change their artwork), and returns None on any
failure so the caller can draw a fallback instead of crashing card
generation. The rest of the card code never talks to the network directly -
only this file does, which is what makes it easy to swap icon providers or
add e.g. a local-file provider later without touching generator.py.
"""
import logging
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

from app.config import get_settings

logger = logging.getLogger("card.assets")
settings = get_settings()


def _cache_path(kind: str, icon_id: int) -> Path:
    d = Path(settings.ICON_CACHE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{kind}_{icon_id}.png"


async def _fetch_and_cache(url: str, cache_path: Path) -> Optional[Image.Image]:
    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGBA")
        except Exception:
            logger.warning(f"Cached icon at {cache_path} is corrupt, refetching")
            cache_path.unlink(missing_ok=True)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.info(f"Icon fetch {url} -> HTTP {resp.status_code}, using fallback")
            return None
        cache_path.write_bytes(resp.content)
        return Image.open(cache_path).convert("RGBA")
    except Exception as exc:
        logger.info(f"Icon fetch {url} failed ({exc}), using fallback")
        return None


async def get_brawler_icon(brawler_id: int) -> Optional[Image.Image]:
    if not brawler_id:
        return None
    url = settings.BRAWLER_ICON_URL_TEMPLATE.format(id=brawler_id)
    return await _fetch_and_cache(url, _cache_path("brawler", brawler_id))


async def get_player_icon(icon_id: Optional[int]) -> Optional[Image.Image]:
    if not icon_id:
        return None
    url = settings.PLAYER_ICON_URL_TEMPLATE.format(id=icon_id)
    return await _fetch_and_cache(url, _cache_path("player", icon_id))
