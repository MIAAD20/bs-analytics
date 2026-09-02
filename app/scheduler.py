"""
Runs collect_leaderboard() on a fixed interval for every configured country
code, inside the same app process - no separate cron job or systemd timer
needed. Started from main.py on FastAPI startup.
"""
import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.api_client import BrawlStarsClient
from app.config import get_settings
from app.database import async_session
from app.leaderboard import collect_leaderboard
from app.redis_client import get_redis

logger = logging.getLogger("scheduler")
settings = get_settings()

scheduler = AsyncIOScheduler()


async def _collect_all_countries():
    redis = await get_redis()
    client = BrawlStarsClient(redis)
    try:
        for country_code in settings.leaderboard_country_list:
            try:
                async with async_session() as session:
                    await collect_leaderboard(session, client, country_code, settings.LEADERBOARD_TOP_N)
            except Exception:
                # one country failing (e.g. bad code, transient API error)
                # must not block the others or crash the scheduler loop
                logger.exception(f"Leaderboard collection failed for country_code={country_code}")
    finally:
        await client.aclose()


def start_scheduler():
    if scheduler.running:
        return
    scheduler.add_job(
        _collect_all_countries,
        trigger=IntervalTrigger(minutes=settings.LEADERBOARD_COLLECT_INTERVAL_MINUTES),
        id="leaderboard_collect",
        replace_existing=True,
        next_run_time=dt.datetime.now(),  # run once immediately on startup, then on interval
        max_instances=1,  # never let two collection runs overlap
    )
    scheduler.start()
    logger.info(
        f"Leaderboard scheduler started: every {settings.LEADERBOARD_COLLECT_INTERVAL_MINUTES}min "
        f"for {settings.leaderboard_country_list}"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
