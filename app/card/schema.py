"""
Input contract for the card generator: a plain PlayerCardData object with
no knowledge of the database, API, or analytics dict shapes. Only
build_card_data() below needs updating if upstream shapes change.

Every displayable field is listed explicitly; if a field is None the
generator hides that element instead of showing a placeholder
("graceful hide").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BrawlerCardEntry:
    name: str
    brawler_id: int
    trophies: int
    power: int


@dataclass
class PlayerCardData:
    # identity (API data)
    name: str
    tag: str
    icon_id: Optional[int] = None

    # headline numbers (API data)
    trophies: Optional[int] = None
    highest_trophies: Optional[int] = None
    exp_level: Optional[int] = None
    club_name: Optional[str] = None

    total_wins: Optional[int] = None  # solo + duo + 3v3 victories summed (API data, summed)

    # derived stats (see app/analytics.py - already computed elsewhere)
    win_rate_pct: Optional[float] = None
    recent_win_rate_pct: Optional[float] = None
    current_streak: Optional[int] = None
    favorite_brawler: Optional[str] = None
    favorite_mode: Optional[str] = None
    performance_score: Optional[float] = None  # labeled as estimate in the card footer

    # estimate (see leaderboard_percentile in analytics.py) - None if no
    # leaderboard snapshot exists yet, in which case the badge is hidden
    global_percentile_estimate: Optional[float] = None

    # Exact global rank - ONLY set when the player is on the API-visible
    # top-200 leaderboard (see player_service.compute_rank_block). Estimates
    # are shown in chat/API text where a note can accompany them; the card
    # shows only exact ranks so it never presents an estimate as fact.
    global_rank: Optional[int] = None

    top_brawlers: list[BrawlerCardEntry] = field(default_factory=list)


def build_card_data(player_summary: dict, analytics: dict, rank: dict | None = None) -> PlayerCardData:
    """
    Adapter: takes the JSON shapes already produced by main.py
    (`player_summary` = the "player" block, `analytics` =
    to_jsonable(full_player_analytics(...)), `rank` = the optional
    to_jsonable rank block from player_service) and maps them onto
    PlayerCardData. This is the ONLY function that needs to change if
    those upstream shapes change.
    """
    wl = analytics.get("win_loss", {})
    tp = analytics.get("trophy_progress", {})
    bb = analytics.get("brawlers", {})
    mm = analytics.get("modes_maps", {})
    perf = analytics.get("performance_score", {})
    lb = analytics.get("leaderboard_estimate", {})

    def _val(d: dict, key: str):
        v = d.get(key)
        return v.get("value") if isinstance(v, dict) else v

    top_brawlers = [
        BrawlerCardEntry(name=b["name"], brawler_id=b["brawler_id"], trophies=b["trophies"], power=b["power"])
        for b in sorted(player_summary.get("brawlers", []), key=lambda b: b["trophies"], reverse=True)
    ]

    rank_global = (rank or {}).get("global") or {}
    global_rank = rank_global.get("value") if rank_global.get("kind") == "api" else None

    return PlayerCardData(
        name=player_summary.get("name", "?"),
        tag=player_summary.get("tag", "?"),
        icon_id=player_summary.get("icon_id"),
        trophies=player_summary.get("trophies") or _val(tp, "current_trophies"),
        highest_trophies=player_summary.get("highest_trophies") or _val(tp, "highest_trophies"),
        exp_level=player_summary.get("exp_level"),
        club_name=player_summary.get("club_name"),
        total_wins=player_summary.get("total_wins"),
        win_rate_pct=_val(wl, "win_rate_pct"),
        recent_win_rate_pct=_val(wl, "recent_win_rate_pct"),
        current_streak=_val(wl, "current_streak"),
        favorite_brawler=_val(bb, "favorite_brawler"),
        favorite_mode=_val(mm, "favorite_mode"),
        performance_score=perf.get("value") if isinstance(perf, dict) else perf,
        global_percentile_estimate=_val(lb, "estimated_percentile_within_pool"),
        global_rank=global_rank,
        top_brawlers=top_brawlers,
    )
