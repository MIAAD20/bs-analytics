"""
Keyword triggers (typing "rate" produces the same response as /rate) are
stored in the bot_triggers database table rather than hard-coded - see
/addtrigger and /removetrigger in handlers.py. This module owns matching
plus a short-lived in-memory cache, avoiding a database query on every
message.

Adding a new trigger ACTION (not just a new keyword for an existing
action) only requires a handler function in handlers.py registered in
ACTION_HANDLERS there; nothing in this module needs to change.
"""
import logging
import re
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import BotTrigger

logger = logging.getLogger("bot.triggers")

# Seeded on first startup only (INSERT ... ON CONFLICT DO NOTHING), so any
# edits/removals an admin makes later persist across restarts - this list
# is just a helpful starting point, not a source of truth.
DEFAULT_TRIGGERS = [
    ("meta", "meta"),
    ("rate", "rate"),
    ("winrate", "rate"),
    ("rank", "rank"),
    ("stats", "player"),
    ("stat", "player"),
    ("brawlers", "brawlers"),
    ("brawler", "brawlers"),
    ("leaderboard", "leaderboard"),
    ("top1000", "leaderboard"),
    ("card", "card"),
    ("help", "help"),
]

_CACHE_TTL_SECONDS = 60
_cache: dict = {"triggers": None, "loaded_at": 0.0}


async def seed_default_triggers(session: AsyncSession):
    for keyword, action in DEFAULT_TRIGGERS:
        await session.execute(
            pg_insert(BotTrigger)
            .values(keyword=keyword, action=action)
            .on_conflict_do_nothing(index_elements=["keyword"])
        )


def invalidate_cache():
    """Call after any add/remove so the change takes effect on the next
    message instead of waiting up to _CACHE_TTL_SECONDS."""
    _cache["triggers"] = None


async def get_triggers(session: AsyncSession) -> dict[str, str]:
    """Returns {keyword: action} for all enabled triggers, cached briefly."""
    now = time.time()
    if _cache["triggers"] is None or (now - _cache["loaded_at"]) > _CACHE_TTL_SECONDS:
        result = await session.execute(select(BotTrigger).where(BotTrigger.enabled == True))  # noqa: E712
        _cache["triggers"] = {row.keyword: row.action for row in result.scalars().all()}
        _cache["loaded_at"] = now
    return _cache["triggers"]


def find_triggered_action(text: str, triggers: dict[str, str]) -> Optional[str]:
    """
    Whole-word match only (so "rate" doesn't fire inside "desperate"). If
    multiple trigger words appear in one message, the first one found wins.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    for word in words:
        if word in triggers:
            return triggers[word]
    return None
