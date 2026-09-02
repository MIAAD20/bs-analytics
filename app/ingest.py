"""
Converts raw Brawl Stars API JSON into rows for the database schema
(see database.py). This is the only module aware of the API's nested
response shape (teams vs. flat player lists, event vs. battle.mode field
inconsistencies across different game modes).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Battle, Brawler, Player, PlayerBrawlerSnapshot, PlayerSnapshot
from app.api_client import normalize_tag
from app.battle_outcome import resolve_battle_outcome


def _parse_battle_time(raw: str) -> dt.datetime:
    # Format: "20230115T142233.000Z"
    return dt.datetime.strptime(raw, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=dt.timezone.utc)


def _find_own_entry(battle: dict, tag: str) -> Optional[dict]:
    """Find the tracked player's entry inside teams[] (3v3/duo) or players[] (showdown)."""
    norm = normalize_tag(tag)
    if "teams" in battle:
        for team in battle["teams"]:
            for p in team:
                if normalize_tag(p.get("tag", "")) == norm:
                    return p
    if "players" in battle:
        for p in battle["players"]:
            if normalize_tag(p.get("tag", "")) == norm:
                return p
    return None


def flatten_battle(raw_item: dict, player_tag: str) -> dict:
    battle_time = _parse_battle_time(raw_item["battleTime"])
    event = raw_item.get("event", {})
    battle = raw_item.get("battle", {})

    mode = battle.get("mode") or event.get("mode")
    map_name = event.get("map") or battle.get("map")
    battle_type = battle.get("type")
    result = battle.get("result")  # None for showdown-style modes
    rank = battle.get("rank")
    duration = battle.get("duration")
    trophy_change = battle.get("trophyChange")

    own = _find_own_entry(battle, player_tag) or {}
    own_brawler = own.get("brawler", {})
    # Solo showdown sometimes puts trophyChange/brawler at top level instead of nested
    if not own_brawler and "brawler" in battle:
        own_brawler = battle.get("brawler", {})

    star_player = battle.get("starPlayer") or {}
    is_star = normalize_tag(star_player.get("tag", "")) == normalize_tag(player_tag) if star_player else False

    dedup_source = json.dumps({
        "t": raw_item["battleTime"],
        "mode": mode,
        "map": map_name,
        "brawler": own_brawler.get("name"),
        "tc": trophy_change,
        "rank": rank,
    }, sort_keys=True)
    dedup_hash = hashlib.sha256(dedup_source.encode()).hexdigest()

    return {
        "battle_time": battle_time,
        "dedup_hash": dedup_hash,
        "mode": mode,
        "map": map_name,
        "battle_type": battle_type,
        "result": result,
        "is_win": resolve_battle_outcome(result, rank, mode),
        "duration": duration,
        "trophy_change": trophy_change,
        "rank": rank,
        "brawler_id": own_brawler.get("id"),
        "brawler_name": own_brawler.get("name"),
        "brawler_power": own_brawler.get("power"),
        "brawler_trophies": own_brawler.get("trophies"),
        "star_player_tag": star_player.get("tag"),
        "is_star_player": is_star,
        "raw": raw_item,
    }


async def upsert_player_snapshot(session: AsyncSession, player_tag: str, api_player: dict) -> PlayerSnapshot:
    tag = normalize_tag(player_tag)

    player = await session.get(Player, tag)
    if player is None:
        player = Player(tag=tag, name=api_player.get("name", "?"))
        session.add(player)
    else:
        player.name = api_player.get("name", player.name)

    club = api_player.get("club") or {}
    snapshot = PlayerSnapshot(
        player_tag=tag,
        name=api_player.get("name", "?"),
        trophies=api_player.get("trophies", 0),
        highest_trophies=api_player.get("highestTrophies", 0),
        exp_level=api_player.get("expLevel", 0),
        exp_points=api_player.get("expPoints", 0),
        solo_victories=api_player.get("soloVictories", 0),
        duo_victories=api_player.get("duoVictories", 0),
        trio_victories=api_player.get("3vs3Victories", 0),
        club_tag=club.get("tag"),
        club_name=club.get("name"),
        club_role=api_player.get("role"),
        icon_id=(api_player.get("icon") or {}).get("id"),
        raw=api_player,
    )
    session.add(snapshot)
    await session.flush()  # get snapshot.id

    for b in api_player.get("brawlers", []):
        # The Brawler reference row must exist before the snapshot row:
        # brawler_id is a FK to brawlers.id, and the execute() below
        # triggers an autoflush that would otherwise insert the snapshot
        # first and violate the constraint for a first-seen brawler.
        await session.execute(
            pg_insert(Brawler)
            .values(id=b.get("id"), name=b.get("name", "?"))
            .on_conflict_do_update(index_elements=["id"], set_={"name": b.get("name", "?")})
        )
        session.add(PlayerBrawlerSnapshot(
            snapshot_id=snapshot.id,
            brawler_id=b.get("id"),
            brawler_name=b.get("name", "?"),
            power=b.get("power", 0),
            rank=b.get("rank", 0),
            trophies=b.get("trophies", 0),
            highest_trophies=b.get("highestTrophies", 0),
            gadgets=b.get("gadgets", []),
            star_powers=b.get("starPowers", []),
            gears=b.get("gears", []),
            hypercharge_level=(b.get("hypercharge") or {}).get("level") if b.get("hypercharge") else None,
        ))

    return snapshot


async def ingest_battlelog(session: AsyncSession, player_tag: str, api_battlelog_items: list[dict]) -> int:
    """Insert new battles, skip ones already stored (unique constraint on
    player_tag+dedup_hash). Returns count of newly inserted rows."""
    tag = normalize_tag(player_tag)
    rows = [flatten_battle(item, tag) for item in api_battlelog_items]
    if not rows:
        return 0

    stmt = pg_insert(Battle).values([{**r, "player_tag": tag} for r in rows])
    stmt = stmt.on_conflict_do_nothing(constraint="uq_battle_dedup")
    result = await session.execute(stmt)
    return result.rowcount or 0


async def get_snapshot_brawlers(session: AsyncSession, snapshot_id: int) -> list[dict]:
    """Brawler list for one snapshot, sorted by trophies desc - used by the
    player analytics endpoint and the Player Card generator."""
    result = await session.execute(
        select(PlayerBrawlerSnapshot)
        .where(PlayerBrawlerSnapshot.snapshot_id == snapshot_id)
        .order_by(PlayerBrawlerSnapshot.trophies.desc())
    )
    rows = result.scalars().all()
    return [
        {"brawler_id": r.brawler_id, "name": r.brawler_name, "trophies": r.trophies, "power": r.power}
        for r in rows
    ]


async def get_recent_battles(session: AsyncSession, player_tag: str, limit: int = 500) -> list[dict]:
    tag = normalize_tag(player_tag)
    result = await session.execute(
        select(Battle).where(Battle.player_tag == tag).order_by(Battle.battle_time.desc()).limit(limit)
    )
    battles = result.scalars().all()
    return [
        {
            "battle_time": b.battle_time.timestamp(),
            "mode": b.mode,
            "map": b.map,
            "result": b.result,
            "rank": b.rank,
            "trophy_change": b.trophy_change,
            "brawler_name": b.brawler_name,
        }
        for b in battles
    ]
