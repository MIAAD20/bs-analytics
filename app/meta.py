"""
Meta/brawler analytics engine: pick rate, win rate, average trophies, and
trend direction per brawler, aggregated in SQL across every stored battle
(optional mode/map filters).

Scope, stated plainly: this reflects only players looked up through this
instance - real, current data, but not a uniform sample of the player
base. Win rates carry sample-size confidence (stats_utils.Confidence),
and "underrated"/"overpicked" are defined heuristics (win rate vs. the
qualified average, pick rate vs. the qualified median), not official
classifications.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Battle
from app.stats_utils import confidence_level

MIN_SAMPLE_META_WINRATE = 15
HIGH_SAMPLE_META_WINRATE = 60
DEFAULT_TOP_N = 10


async def get_brawler_meta(
    session: AsyncSession,
    mode: Optional[str] = None,
    map_name: Optional[str] = None,
) -> list[dict]:
    """
    Returns pick rate, win rate, sample size, and average trophies per
    brawler, optionally scoped to one mode and/or map. Supports the
    "Brawler x Mode x Map" analysis described in the project spec by
    calling this with both `mode` and `map_name` set.
    """
    query = (
        select(
            Battle.brawler_name,
            func.count().label("pick_count"),
            func.count(Battle.is_win).label("resolved"),
            func.sum(case((Battle.is_win.is_(True), 1), else_=0)).label("wins"),
            func.avg(Battle.brawler_trophies).label("avg_trophies"),
        )
        .where(Battle.brawler_name.isnot(None))
    )
    if mode:
        query = query.where(Battle.mode == mode)
    if map_name:
        query = query.where(Battle.map == map_name)
    query = query.group_by(Battle.brawler_name)

    rows = (await session.execute(query)).all()
    total_picks = sum(r.pick_count for r in rows) or 1

    results = []
    for r in rows:
        win_rate = (r.wins / r.resolved * 100) if r.resolved else None
        results.append({
            "brawler_name": r.brawler_name,
            "pick_count": r.pick_count,
            "pick_rate_pct": round(r.pick_count / total_picks * 100, 2),
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "sample_size": r.resolved,
            "confidence": confidence_level(r.resolved, MIN_SAMPLE_META_WINRATE, HIGH_SAMPLE_META_WINRATE).value,
            "avg_trophies": round(r.avg_trophies, 1) if r.avg_trophies is not None else None,
        })
    return sorted(results, key=lambda b: b["pick_count"], reverse=True)


async def get_brawler_mode_map_breakdown(session: AsyncSession, brawler_name: str) -> list[dict]:
    """Per (mode, map) pick rate and win rate for a single brawler."""
    query = (
        select(
            Battle.mode,
            Battle.map,
            func.count().label("pick_count"),
            func.count(Battle.is_win).label("resolved"),
            func.sum(case((Battle.is_win.is_(True), 1), else_=0)).label("wins"),
        )
        .where(Battle.brawler_name == brawler_name)
        .group_by(Battle.mode, Battle.map)
    )
    rows = (await session.execute(query)).all()

    results = []
    for r in rows:
        win_rate = (r.wins / r.resolved * 100) if r.resolved else None
        results.append({
            "mode": r.mode,
            "map": r.map,
            "pick_count": r.pick_count,
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "sample_size": r.resolved,
            "confidence": confidence_level(r.resolved, MIN_SAMPLE_META_WINRATE, HIGH_SAMPLE_META_WINRATE).value,
        })
    return sorted(results, key=lambda b: b["pick_count"], reverse=True)


async def _window_counts(session: AsyncSession, start: dt.datetime, end: dt.datetime) -> dict[str, dict]:
    query = (
        select(
            Battle.brawler_name,
            func.count().label("pick_count"),
            func.count(Battle.is_win).label("resolved"),
            func.sum(case((Battle.is_win.is_(True), 1), else_=0)).label("wins"),
        )
        .where(Battle.brawler_name.isnot(None), Battle.battle_time >= start, Battle.battle_time < end)
        .group_by(Battle.brawler_name)
    )
    rows = (await session.execute(query)).all()
    total = sum(r.pick_count for r in rows) or 1
    return {
        r.brawler_name: {
            "pick_rate_pct": round(r.pick_count / total * 100, 2),
            "win_rate_pct": round(r.wins / r.resolved * 100, 1) if r.resolved else None,
            "sample_size": r.resolved,
        }
        for r in rows
    }


async def get_trending_brawlers(
    session: AsyncSession,
    recent_days: int = 7,
    previous_days: int = 7,
) -> list[dict]:
    """
    Compares pick rate and win rate between two consecutive time windows
    (default: the last 7 days vs. the 7 days before that) to surface
    which brawlers are rising or falling in usage. Requires battle_time
    timestamps spread across both windows - on a freshly deployed
    instance, this will be sparse until enough history accumulates.
    """
    now = dt.datetime.now(dt.timezone.utc)
    recent_start = now - dt.timedelta(days=recent_days)
    previous_start = recent_start - dt.timedelta(days=previous_days)

    recent = await _window_counts(session, recent_start, now)
    previous = await _window_counts(session, previous_start, recent_start)

    empty = {"pick_rate_pct": 0, "win_rate_pct": None, "sample_size": 0}
    trending = []
    for name in set(recent) | set(previous):
        r = recent.get(name, empty)
        p = previous.get(name, empty)
        trending.append({
            "brawler_name": name,
            "pick_rate_pct_recent": r["pick_rate_pct"],
            "pick_rate_pct_previous": p["pick_rate_pct"],
            "pick_rate_delta": round(r["pick_rate_pct"] - p["pick_rate_pct"], 2),
            "win_rate_pct_recent": r["win_rate_pct"],
            "win_rate_pct_previous": p["win_rate_pct"],
            "sample_size_recent": r["sample_size"],
        })
    return sorted(trending, key=lambda b: b["pick_rate_delta"], reverse=True)


def classify_meta_tiers(
    meta: list[dict],
    min_sample: int = MIN_SAMPLE_META_WINRATE,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """
    Derives most-popular/underrated/overpicked groupings from the output
    of get_brawler_meta(). Pure function with no database access, so it
    can be tested independently of the aggregation query above.

    "Underrated" = win rate above the qualified-brawler average while
    pick rate is below the qualified-brawler median.
    "Overpicked" = the inverse: pick rate above median, win rate below
    average. Both exclude brawlers below `min_sample` resolved battles to
    avoid a two-battle 100% win rate skewing the result.
    """
    most_popular = sorted(meta, key=lambda b: b["pick_count"], reverse=True)[:top_n]

    qualified = [b for b in meta if b["sample_size"] >= min_sample and b["win_rate_pct"] is not None]
    if not qualified:
        return {"most_popular": most_popular, "underrated": [], "overpicked": []}

    avg_win_rate = sum(b["win_rate_pct"] for b in qualified) / len(qualified)
    sorted_pick_rates = sorted(b["pick_rate_pct"] for b in qualified)
    median_pick_rate = sorted_pick_rates[len(sorted_pick_rates) // 2]

    underrated = sorted(
        (b for b in qualified if b["win_rate_pct"] > avg_win_rate and b["pick_rate_pct"] < median_pick_rate),
        key=lambda b: b["win_rate_pct"],
        reverse=True,
    )[:top_n]
    overpicked = sorted(
        (b for b in qualified if b["pick_rate_pct"] > median_pick_rate and b["win_rate_pct"] < avg_win_rate),
        key=lambda b: b["pick_rate_pct"],
        reverse=True,
    )[:top_n]

    return {"most_popular": most_popular, "underrated": underrated, "overpicked": overpicked}
