"""
The one place that fetches from Brawl Stars, stores it, and computes
analytics for a player. Both main.py (HTTP API) and the Telegram bot call
this - neither contains its own copy of the pipeline. Raises plain
exceptions (PlayerNotFoundError, BrawlStarsAPIError from api_client.py),
not framework-specific ones - callers translate those into HTTP errors or
chat messages as appropriate for their context.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import estimate_rank_from_pool, full_player_analytics
from app.api_client import BrawlStarsClient, normalize_tag
from app.database import LeaderboardCurrent, LeaderboardSnapshot, async_session
from app.ingest import get_recent_battles, get_snapshot_brawlers, ingest_battlelog, upsert_player_snapshot
from app.redis_client import get_redis
from app.serialize import to_jsonable
from app.stats_utils import Stat


async def fetch_ingest_analyze(tag: str) -> dict:
    """
    Fetch a player + battlelog from the Brawl Stars API, store the
    snapshot/battles, and return player summary + full analytics.
    Raises PlayerNotFoundError / BrawlStarsAPIError (see api_client.py) on
    failure - callers are responsible for handling those.
    """
    tag = normalize_tag(tag)
    redis = await get_redis()
    client = BrawlStarsClient(redis)

    try:
        api_player = await client.get_player(tag)
        api_battlelog = await client.get_battlelog(tag)
    finally:
        await client.aclose()

    async with async_session() as session:
        snapshot = await upsert_player_snapshot(session, tag, api_player)
        inserted = await ingest_battlelog(session, tag, api_battlelog.get("items", []))
        await session.commit()

        battles = await get_recent_battles(session, tag)
        brawlers = await get_snapshot_brawlers(session, snapshot.id)

        # Trophy pool for leaderboard_percentile(): the most recent snapshot
        # rows (up to ~5 runs of the tracked top-200).
        latest_lb = await session.execute(
            select(LeaderboardSnapshot.trophies)
            .where(LeaderboardSnapshot.country_code == "global")
            .order_by(LeaderboardSnapshot.fetched_at.desc())
            .limit(1000)
        )
        leaderboard_pool_trophies = [row[0] for row in latest_lb.all()]

        rank_block = await compute_rank_block(session, tag, snapshot.trophies)

    analytics = full_player_analytics(
        snapshot={"trophies": snapshot.trophies, "highest_trophies": snapshot.highest_trophies},
        battles=battles,
        pool_trophies=leaderboard_pool_trophies,
    )

    player_summary = {
        "tag": tag,
        "name": snapshot.name,
        "trophies": snapshot.trophies,
        "highest_trophies": snapshot.highest_trophies,
        "exp_level": snapshot.exp_level,
        "club_name": snapshot.club_name,
        "icon_id": snapshot.icon_id,
        "total_wins": snapshot.solo_victories + snapshot.duo_victories + snapshot.trio_victories,
        "brawlers": brawlers,
    }

    return {
        "player": player_summary,
        "new_battles_stored": inserted,
        "battles_in_history": len(battles),
        "analytics": to_jsonable(analytics),
        "rank": to_jsonable(rank_block),
    }


async def compute_rank_block(session: AsyncSession, tag: str, trophies: int) -> dict:
    """
    The player's rank(s), as far as they can honestly be known. The official
    API serves only the top 200 entries of any player rankings board, so:
      - "global": EXACT rank from the tracked table for players on the
        visible top-200 (tagged "api"), else the conservative estimate from
        estimate_rank_from_pool (tagged "estimate")
      - "local": same per country. The API never says which country a
        player belongs to, so a local rank only resolves when the player
        appears on a country board this instance has collected (e.g. via
        the bot's /leaderboard <country>) - that is Supercell's own
        attribution, so it is exact. Otherwise None with a note - never
        guessed from the global curve.
    Returns {"global": Stat, "local_country": str | None, "local": Stat}.
    """
    result = await session.execute(
        select(LeaderboardCurrent.rank, LeaderboardCurrent.player_tag, LeaderboardCurrent.trophies)
        .where(LeaderboardCurrent.country_code == "global")
        .order_by(LeaderboardCurrent.rank)
    )
    pool = result.all()
    if not pool:
        global_stat = Stat(None, "estimate", note="no leaderboard data collected yet")
    elif tag in {row.player_tag for row in pool}:
        ranks_by_tag = {row.player_tag: row.rank for row in pool}
        global_stat = Stat(
            ranks_by_tag[tag], "api",
            note="exact rank from the API-visible global top-200 leaderboard",
        )
    else:
        global_stat = estimate_rank_from_pool(trophies, [(row.rank, row.trophies) for row in pool])

    local_result = await session.execute(
        select(LeaderboardCurrent.country_code, LeaderboardCurrent.rank, LeaderboardCurrent.player_tag)
        .where(LeaderboardCurrent.country_code != "global")
    )
    local_rows = local_result.all()
    local_hits = {row.player_tag: (row.country_code, row.rank) for row in local_rows}
    if tag in local_hits:
        local_country, local_rank = local_hits[tag]
        deepest = max(row.rank for row in local_rows if row.country_code == local_country)
        local_stat = Stat(
            local_rank, "api",
            note=f"exact rank from the API-visible {local_country.upper()} top-{deepest} leaderboard",
        )
    elif local_rows:
        local_country = None
        local_stat = Stat(
            None, "estimate",
            note=("not on any collected country top-200; the API does not expose a "
                  "player's country - collect a board with /leaderboard <country>"),
        )
    else:
        local_country = None
        local_stat = Stat(
            None, "estimate",
            note="no country leaderboard collected yet (e.g. /leaderboard eg)",
        )

    return {"global": global_stat, "local_country": local_country, "local": local_stat}


async def get_player_rank(tag: str) -> dict:
    """
    Lightweight rank lookup for the /player/{tag}/rank endpoint and the
    bot's /rank command: fetches only the player profile (no battlelog,
    no snapshot/battle writes), then resolves the rank block against the
    tracked leaderboard.
    """
    tag = normalize_tag(tag)
    redis = await get_redis()
    client = BrawlStarsClient(redis)
    try:
        api_player = await client.get_player(tag)
    finally:
        await client.aclose()

    async with async_session() as session:
        rank_block = await compute_rank_block(session, tag, api_player.get("trophies", 0))

    return {
        "tag": tag,
        "name": api_player.get("name", "?"),
        "trophies": api_player.get("trophies", 0),
        "rank": to_jsonable(rank_block),
    }
