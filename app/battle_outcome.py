"""
Single source of truth for classifying a battle as a win, loss, or
unresolved. Used by ingest.py (populates Battle.is_win at storage time)
and by analytics.py (per-player win rate, streaks, etc.), so the two never
disagree on what counts as a win.
"""
from typing import Optional


def resolve_battle_outcome(result: Optional[str], rank: Optional[int], mode: Optional[str]) -> Optional[bool]:
    """
    Determines whether a battle was a win.

    Team modes (Gem Grab, Brawl Ball, etc.) report a `result` field of
    "victory"/"defeat"/"draw". Showdown-style modes report a `rank`
    placement instead: 1st place counts as a win in solo showdown, top 2
    in duo showdown. Returns None when neither field allows a confident
    classification (e.g. an unrecognized event type) - callers must treat
    None as "exclude from win/loss math", never guess a value for it.
    """
    if result in ("victory", "defeat", "draw"):
        if result == "victory":
            return True
        if result == "defeat":
            return False
        return None  # draw - counts toward neither wins nor losses

    if rank is not None:
        if mode and "duo" in mode.lower():
            return rank <= 2
        return rank == 1

    return None
