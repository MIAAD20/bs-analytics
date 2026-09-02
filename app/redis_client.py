from redis.asyncio import Redis, from_url

from app.config import get_settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = from_url(settings.REDIS_URL, decode_responses=True)
    return _redis
