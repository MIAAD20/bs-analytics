"""
Top-N leaderboard collection + analytics.

Three tables work together:
  - LeaderboardCurrent          live membership, exactly one row per tracked
                                  player right now (updated in place)
  - LeaderboardMembershipEvent  audit log: every time a player enters or
                                  exits the tracked pool
  - LeaderboardSnapshot         full periodic dump of the whole pool, kept
                                  forever, used for trend charts over time

collect_leaderboard() is the one function that keeps all three in sync.
Call it on a schedule (see scheduler.py) or manually via the API.
"""
from __future__ import annotations

import datetime as dt
import logging
import statistics

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_client import BrawlStarsAPIError, BrawlStarsClient, normalize_tag
from app.database import LeaderboardCurrent, LeaderboardMembershipEvent, LeaderboardSnapshot

logger = logging.getLogger("leaderboard")

RANK_CUTOFF_POINTS = (1, 10, 50, 100, 250, 500, 750, 1000)


async def collect_leaderboard(
    session: AsyncSession,
    client: BrawlStarsClient,
    country_code: str = "global",
    top_n: int = 1000,
) -> dict:
    """
    Fetches the current top-N, diffs it against what's stored, and:
      - adds new rows to LeaderboardCurrent for players newly in range
      - removes rows for players who fell out of range
      - updates rank/trophies in place for players still in range
      - logs an 'entered' or 'exited' event for every membership change
      - appends one full LeaderboardSnapshot row per player (time series)

    Safe to call repeatedly / on a schedule - it's a full upsert each time,
    not additive-only, so LeaderboardCurrent always reflects reality.
    """
    items = await client.get_top_players(country_code, n=top_n)
    if not items:
        logger.warning(f"No leaderboard data returned for {country_code}")
        return {"country_code": country_code, "total_tracked": 0, "entered_count": 0, "exited_count": 0}

    fetched_at = dt.datetime.now(dt.timezone.utc)
    fetched_map: dict[str, dict] = {}
    for idx, it in enumerate(items, start=1):
        tag = normalize_tag(it.get("tag", ""))
        fetched_map[tag] = {
            "rank": it.get("rank", idx),
            "name": it.get("name", "?"),
            "trophies": it.get("trophies", 0),
            "club_name": (it.get("club") or {}).get("name"),
            "icon_id": (it.get("icon") or {}).get("id"),
        }

    existing_result = await session.execute(
        select(LeaderboardCurrent).where(LeaderboardCurrent.country_code == country_code)
    )
    existing_rows = {row.player_tag: row for row in existing_result.scalars().all()}

    fetched_tags = set(fetched_map.keys())
    existing_tags = set(existing_rows.keys())

    entered_tags = fetched_tags - existing_tags
    exited_tags = existing_tags - fetched_tags
    continuing_tags = fetched_tags & existing_tags

    events: list[LeaderboardMembershipEvent] = []

    for tag in entered_tags:
        d = fetched_map[tag]
        session.add(LeaderboardCurrent(country_code=country_code, player_tag=tag, **d))
        events.append(LeaderboardMembershipEvent(
            country_code=country_code, player_tag=tag, name=d["name"], event_type="entered",
            rank=d["rank"], trophies=d["trophies"], occurred_at=fetched_at,
        ))

    exited_summary = []
    for tag in exited_tags:
        row = existing_rows[tag]
        exited_summary.append({"tag": tag, "name": row.name, "last_rank": row.rank, "last_trophies": row.trophies})
        events.append(LeaderboardMembershipEvent(
            country_code=country_code, player_tag=tag, name=row.name, event_type="exited",
            rank=row.rank, trophies=row.trophies, occurred_at=fetched_at,
        ))
        await session.delete(row)

    for tag in continuing_tags:
        row = existing_rows[tag]
        d = fetched_map[tag]
        row.rank = d["rank"]
        row.name = d["name"]
        row.trophies = d["trophies"]
        row.club_name = d["club_name"]
        row.icon_id = d["icon_id"]

    session.add_all(events)

    session.add_all([
        LeaderboardSnapshot(
            fetched_at=fetched_at, country_code=country_code, rank=d["rank"], player_tag=tag,
            name=d["name"], trophies=d["trophies"], club_name=d["club_name"], icon_id=d["icon_id"],
        )
        for tag, d in fetched_map.items()
    ])

    await session.commit()

    logger.info(
        f"leaderboard[{country_code}] collected: {len(fetched_map)} tracked, "
        f"+{len(entered_tags)} entered, -{len(exited_tags)} exited"
    )

    return {
        "country_code": country_code,
        "total_tracked": len(fetched_map),
        "entered_count": len(entered_tags),
        "exited_count": len(exited_tags),
        "entered": [{"tag": t, **fetched_map[t]} for t in entered_tags],
        "exited": exited_summary,
        "collected_at": fetched_at.isoformat(),
    }


async def get_current_top(session: AsyncSession, country_code: str = "global", limit: int = 1000) -> list[dict]:
    result = await session.execute(
        select(LeaderboardCurrent)
        .where(LeaderboardCurrent.country_code == country_code)
        .order_by(LeaderboardCurrent.rank)
        .limit(limit)
    )
    return [
        {
            "rank": r.rank, "tag": r.player_tag, "name": r.name, "trophies": r.trophies,
            "club_name": r.club_name, "icon_id": r.icon_id, "updated_at": r.updated_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


# A stored pool older than this is refreshed before being served to the bot.
# Scheduled countries are refreshed every LEADERBOARD_COLLECT_INTERVAL_MINUTES
# (30 by default), so in practice only on-demand countries ever hit the
# re-collect branch.
FRESH_AFTER = dt.timedelta(hours=1)


async def get_top_fresh(
    session: AsyncSession,
    client: BrawlStarsClient,
    country_code: str,
    limit: int = 10,
    top_n: int = 200,
    max_age: dt.timedelta = FRESH_AFTER,
) -> list[dict]:
    """
    Tracked-pool read with an on-demand collection fallback, so the bot's
    /leaderboard command works for ANY country code, not just the ones the
    scheduler collects (which is normally just "global").

      - rows exist and were refreshed within `max_age`: return them as-is
      - rows exist but are stale: re-collect first; if that API call fails,
        the stored rows are still returned (stale beats nothing)
      - no rows at all: collect the country's visible top-200 right now -
        through the exact same upsert/membership pipeline as the scheduled
        run, so everything is stored consistently - then read

    Raises BrawlStarsAPIError only when there is no stored data AND the live
    collection fails (e.g. an invalid country code -> 404), so callers can
    show a precise error message.
    """
    players = await get_current_top(session, country_code, limit)
    if players:
        newest = max(dt.datetime.fromisoformat(p["updated_at"]) for p in players)
        if dt.datetime.now(dt.timezone.utc) - newest <= max_age:
            return players
        try:
            await collect_leaderboard(session, client, country_code, top_n)
        except BrawlStarsAPIError:
            logger.warning(f"leaderboard[{country_code}] refresh failed; serving stored rows")
            return players
    else:
        await collect_leaderboard(session, client, country_code, top_n)
    return await get_current_top(session, country_code, limit)


async def get_recent_events(session: AsyncSession, country_code: str = "global", limit: int = 50) -> list[dict]:
    result = await session.execute(
        select(LeaderboardMembershipEvent)
        .where(LeaderboardMembershipEvent.country_code == country_code)
        .order_by(LeaderboardMembershipEvent.occurred_at.desc())
        .limit(limit)
    )
    return [
        {
            "tag": e.player_tag, "name": e.name, "event_type": e.event_type,
            "rank": e.rank, "trophies": e.trophies, "occurred_at": e.occurred_at.isoformat(),
        }
        for e in result.scalars().all()
    ]


async def get_top1000_stats(session: AsyncSession, country_code: str = "global") -> dict:
    """
    API data (from LeaderboardCurrent) + derived aggregate stats: mean,
    median, spread, rank cutoffs, average gap between consecutive ranks.
    Everything here is "derived" - directly computed from the tracked pool,
    no estimation involved (unlike leaderboard_percentile() for a single
    player in analytics.py, which extrapolates beyond the pool).
    """
    result = await session.execute(
        select(LeaderboardCurrent.rank, LeaderboardCurrent.trophies)
        .where(LeaderboardCurrent.country_code == country_code)
        .order_by(LeaderboardCurrent.rank)
    )
    rows = result.all()
    if not rows:
        return {"count": 0, "note": "no leaderboard data collected yet for this country"}

    ranks = [r.rank for r in rows]
    trophies = [r.trophies for r in rows]
    n = len(trophies)

    cutoffs = {}
    for target in RANK_CUTOFF_POINTS:
        if target > max(ranks):
            continue
        candidates = [(rk, tr) for rk, tr in zip(ranks, trophies) if rk <= target]
        cutoffs[target] = candidates[-1][1] if candidates else None

    gaps = [trophies[i] - trophies[i + 1] for i in range(n - 1)]

    return {
        "count": n,
        "mean_trophies": round(statistics.mean(trophies), 1),
        "median_trophies": statistics.median(trophies),
        "stdev_trophies": round(statistics.pstdev(trophies), 1) if n > 1 else 0,
        "min_trophies": min(trophies),
        "max_trophies": max(trophies),
        "trophy_range": max(trophies) - min(trophies),
        "rank_cutoffs": cutoffs,
        "avg_gap_between_consecutive_ranks": round(statistics.mean(gaps), 2) if gaps else 0,
    }
