"""
Central configuration. All secrets come from environment variables (.env file
on the VPS, never committed to git). See .env.example for the full list.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Brawl Stars API (get token at https://developer.brawlstars.com)
    BRAWL_STARS_API_TOKEN: str
    BRAWL_STARS_API_BASE: str = "https://api.brawlstars.com/v1"

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    # Telegram user ID of the bot owner (a number - obtained by messaging
    # @userinfobot on Telegram). Enables owner-only bot commands
    # (/addtrigger, /removetrigger) for managing keyword triggers without
    # redeploying. Leave unset to disable those commands entirely.
    TELEGRAM_BOT_OWNER_ID: Optional[int] = None

    # Database (Postgres)
    DATABASE_URL: str  # e.g. postgresql+asyncpg://user:pass@postgres:5432/bsanalytics

    # Redis (cache + rate-limit protection)
    REDIS_URL: str  # e.g. redis://redis:6379/0

    # App
    SECRET_KEY: str  # used for admin panel session tokens later
    ENV: str = "production"
    LOG_LEVEL: str = "INFO"

    # Cache TTLs (seconds)
    PLAYER_CACHE_TTL: int = 60          # player profile changes fast during play
    BATTLELOG_CACHE_TTL: int = 60
    RANKINGS_CACHE_TTL: int = 900       # leaderboard moves slower
    BRAWLERS_CACHE_TTL: int = 86400     # static-ish reference data

    # Rate limiting (Brawl Stars API allows a generous but finite rate; stay safe)
    API_MAX_REQUESTS_PER_SECOND: int = 8

    # Leaderboard collector
    LEADERBOARD_COUNTRIES: str = "global"  # comma-separated country codes, e.g. "global,US,BR"
    LEADERBOARD_TOP_N: int = 1000
    LEADERBOARD_COLLECT_INTERVAL_MINUTES: int = 30

    @property
    def leaderboard_country_list(self) -> list[str]:
        return [c.strip() for c in self.LEADERBOARD_COUNTRIES.split(",") if c.strip()]

    # Player Card icons. The official Brawl Stars API only returns numeric
    # brawler/icon IDs, not downloadable images - Supercell has no public
    # image CDN. Brawlify (community-run, widely used, free) maps those IDs
    # to icon PNGs. Swap providers any time by changing these two URLs -
    # no code change needed. {id} is replaced with the brawler_id / icon_id.
    BRAWLER_ICON_URL_TEMPLATE: str = "https://cdn.brawlify.com/brawlers/borderless/{id}.png"
    PLAYER_ICON_URL_TEMPLATE: str = "https://cdn.brawlify.com/player-icons/regular/{id}.png"
    ICON_CACHE_DIR: str = "/srv/cache/icons"


@lru_cache
def get_settings() -> Settings:
    return Settings()
