"""
HTTP API entrypoint: player analytics, the Player Card generator, the
leaderboard tracker, and the meta/brawler analytics engine. Also starts
the Telegram bot and the leaderboard collection scheduler on startup.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import select

from app.api_client import BrawlStarsAPIError, BrawlStarsClient, PlayerNotFoundError
from app.bot.runner import start_bot, stop_bot
from app.card.generator import generate_player_card
from app.card.schema import build_card_data
from app.card.theme import CARD_SIZES
from app.config import get_settings
from app.database import async_session, init_db
from app.leaderboard import collect_leaderboard, get_current_top, get_recent_events, get_top1000_stats
from app.meta import classify_meta_tiers, get_brawler_meta, get_brawler_mode_map_breakdown, get_trending_brawlers
from app.redis_client import get_redis
from app.scheduler import start_scheduler, stop_scheduler
from app.services.player_service import fetch_ingest_analyze, get_player_rank

logging.basicConfig(level=get_settings().LOG_LEVEL)
logger = logging.getLogger("main")

app = FastAPI(title="Brawl Stars Analytics API", version="0.1.0")


@app.on_event("startup")
async def startup():
    await init_db()
    start_scheduler()
    try:
        await start_bot()
    except Exception:
        # A bot startup failure (bad token, no network yet, etc.) should
        # never take down the HTTP API - log it and keep serving requests.
        logger.exception("Telegram bot failed to start - API is running without it")


@app.on_event("shutdown")
async def shutdown():
    stop_scheduler()
    try:
        await stop_bot()
    except Exception:
        logger.exception("Error stopping Telegram bot")


@app.get("/health")
async def health():
    checks = {"database": "unknown", "redis": "unknown"}
    try:
        async with async_session() as session:
            await session.execute(select(1))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


@app.get("/player/{tag}")
async def get_player_analytics(tag: str):
    try:
        return await fetch_ingest_analyze(tag)
    except PlayerNotFoundError:
        raise HTTPException(404, f"Player {tag} not found")
    except BrawlStarsAPIError as exc:
        raise HTTPException(502, f"Brawl Stars API error: {exc}")


@app.get("/player/{tag}/card")
async def get_player_card(tag: str, variant: str = "telegram"):
    """
    Returns the generated Player Card as a PNG image. `variant` is one of
    theme.CARD_SIZES ("telegram", "square", "desktop") - add more variants
    in card/theme.py and they become usable here immediately.
    """
    if variant not in CARD_SIZES:
        raise HTTPException(400, f"Unknown variant '{variant}'. Options: {list(CARD_SIZES)}")

    try:
        result = await fetch_ingest_analyze(tag)
    except PlayerNotFoundError:
        raise HTTPException(404, f"Player {tag} not found")
    except BrawlStarsAPIError as exc:
        raise HTTPException(502, f"Brawl Stars API error: {exc}")

    card_data = build_card_data(result["player"], result["analytics"], result.get("rank"))
    png_bytes = await generate_player_card(card_data, variant)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/player/{tag}/rank")
async def get_player_rank_endpoint(tag: str):
    """
    The player's global leaderboard rank. Exact on the API-visible top-200
    leaderboard; a labeled extrapolated estimate beyond it (Supercell does
    not publish deeper rankings - see player_service.compute_rank_block).
    """
    try:
        return await get_player_rank(tag)
    except PlayerNotFoundError:
        raise HTTPException(404, f"Player {tag} not found")
    except BrawlStarsAPIError as exc:
        raise HTTPException(502, f"Brawl Stars API error: {exc}")


# ---- Top 1000 leaderboard -------------------------------------------------

@app.get("/leaderboard/{country_code}/top1000")
async def leaderboard_top1000(country_code: str, limit: int = 1000):
    async with async_session() as session:
        return {"country_code": country_code, "players": await get_current_top(session, country_code, limit)}


@app.get("/leaderboard/{country_code}/changes")
async def leaderboard_changes(country_code: str, limit: int = 50):
    """Recent entries/exits from the tracked top-N (audit log)."""
    async with async_session() as session:
        return {"country_code": country_code, "events": await get_recent_events(session, country_code, limit)}


@app.get("/leaderboard/{country_code}/stats")
async def leaderboard_stats(country_code: str):
    async with async_session() as session:
        return await get_top1000_stats(session, country_code)


@app.post("/leaderboard/{country_code}/collect")
async def leaderboard_collect_now(country_code: str):
    """Manually trigger a collection run right now, instead of waiting for
    the scheduled interval. Useful for testing after first deploy."""
    settings = get_settings()
    redis = await get_redis()
    client = BrawlStarsClient(redis)
    try:
        async with async_session() as session:
            result = await collect_leaderboard(session, client, country_code, settings.LEADERBOARD_TOP_N)
        return result
    except BrawlStarsAPIError as exc:
        raise HTTPException(502, f"Brawl Stars API error: {exc}")
    finally:
        await client.aclose()


# ---- Meta / brawler analytics ---------------------------------------------

@app.get("/meta/brawlers")
async def meta_brawlers(mode: Optional[str] = None, map: Optional[str] = None):
    """Pick rate, win rate, and average trophies per brawler across all
    stored battle records, optionally filtered by mode and/or map."""
    async with async_session() as session:
        return {"brawlers": await get_brawler_meta(session, mode, map)}


@app.get("/meta/brawlers/{brawler_name}/breakdown")
async def meta_brawler_breakdown(brawler_name: str):
    """Per (mode, map) pick rate and win rate for a single brawler."""
    async with async_session() as session:
        return {"brawler_name": brawler_name, "breakdown": await get_brawler_mode_map_breakdown(session, brawler_name)}


@app.get("/meta/trending")
async def meta_trending(recent_days: int = 7, previous_days: int = 7):
    """Pick rate / win rate change between two consecutive time windows."""
    async with async_session() as session:
        return {"trending": await get_trending_brawlers(session, recent_days, previous_days)}


@app.get("/meta/tiers")
async def meta_tiers(mode: Optional[str] = None, map: Optional[str] = None):
    """Most-popular / underrated / overpicked groupings - see
    meta.classify_meta_tiers for the exact definition of each group."""
    async with async_session() as session:
        brawlers = await get_brawler_meta(session, mode, map)
    return classify_meta_tiers(brawlers)
