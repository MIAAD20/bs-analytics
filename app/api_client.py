"""
Thin, resilient wrapper around the official Brawl Stars API: rate limiting,
response caching, retries, and tag normalization. No analytics logic here -
see analytics.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote

import httpx
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("bs_api_client")

settings = get_settings()


class BrawlStarsAPIError(Exception):
    """Raised for non-recoverable API errors (bad tag, invalid token, etc.)."""

    def __init__(self, status_code: int, message: str, reason: Optional[str] = None):
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"[{status_code}] {message}")


class PlayerNotFoundError(BrawlStarsAPIError):
    pass


def normalize_tag(tag: str) -> str:
    """
    Player/club tags come in as '#2Y8V0YQ8', '2Y8V0YQ8', or lowercase.
    API wants uppercase, no leading '#', then URL-encoded to %23 when used in path.
    """
    tag = tag.strip().upper().replace("O", "0")  # BS tags never use letter O, only digit 0
    if not tag.startswith("#"):
        tag = "#" + tag
    return tag


def _encoded_tag(tag: str) -> str:
    return quote(normalize_tag(tag))


class _TokenBucket:
    """Redis-backed fixed-window rate limiter shared by all workers."""

    def __init__(self, redis: Redis, rate_per_sec: int):
        self.redis = redis
        self.rate = rate_per_sec

    async def acquire(self):
        key = "bs_api:rate_bucket"
        now_window = int(time.time())
        window_key = f"{key}:{now_window}"
        count = await self.redis.incr(window_key)
        if count == 1:
            await self.redis.expire(window_key, 1)
        if count > self.rate:
            await asyncio.sleep(1 - (time.time() - now_window))


class BrawlStarsClient:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.bucket = _TokenBucket(redis, settings.API_MAX_REQUESTS_PER_SECOND)
        self._client = httpx.AsyncClient(
            base_url=settings.BRAWL_STARS_API_BASE,
            headers={
                "Authorization": f"Bearer {settings.BRAWL_STARS_API_TOKEN}",
                "Accept": "application/json",
            },
            timeout=10.0,
        )

    async def aclose(self):
        await self._client.aclose()

    async def _get(self, path: str, cache_key: Optional[str] = None, ttl: int = 60) -> dict[str, Any]:
        if cache_key:
            cached = await self.redis.get(cache_key)
            if cached:
                return json.loads(cached)

        await self.bucket.acquire()

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = await self._client.get(path)
                if resp.status_code == 200:
                    data = resp.json()
                    if cache_key:
                        await self.redis.set(cache_key, json.dumps(data), ex=ttl)
                    return data
                if resp.status_code == 404:
                    raise PlayerNotFoundError(404, "Not found", reason="notFound")
                if resp.status_code == 403:
                    raise BrawlStarsAPIError(403, "Access denied - check API token / IP allowlist", reason="accessDenied")
                if resp.status_code == 429:
                    # Server-side rate limit hit despite the local token bucket; back off and retry.
                    logger.warning("Brawl Stars API 429, backing off")
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code in (500, 503):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise BrawlStarsAPIError(resp.status_code, resp.text)
            except httpx.TransportError as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
        if last_exc:
            raise BrawlStarsAPIError(0, f"Network error after retries: {last_exc}")
        raise BrawlStarsAPIError(429, "Rate limited after retries")

    # ---- Public endpoints -------------------------------------------------

    async def get_player(self, tag: str) -> dict[str, Any]:
        t = _encoded_tag(tag)
        return await self._get(
            f"/players/{t}",
            cache_key=f"bs:player:{normalize_tag(tag)}",
            ttl=settings.PLAYER_CACHE_TTL,
        )

    async def get_battlelog(self, tag: str) -> dict[str, Any]:
        t = _encoded_tag(tag)
        return await self._get(
            f"/players/{t}/battlelog",
            cache_key=f"bs:battlelog:{normalize_tag(tag)}",
            ttl=settings.BATTLELOG_CACHE_TTL,
        )

    async def get_rankings_players_page(
        self, country_code: str = "global", limit: int = 200, after: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Single rankings page (no pagination loop - see get_top_players for
        that). NOTE: the API caps any rankings response at 200 items and
        returns NO 'after' cursor when limit >= 200, so callers that need
        to paginate must keep limit below 200.
        """
        path = f"/rankings/{country_code}/players?limit={limit}"
        if after:
            # 'after' is an opaque cursor from a previous response
            # (base64 like eyJwb3MiOjEwMH0) - encode defensively anyway.
            path += f"&after={quote(after)}"
        return await self._get(
            path,
            cache_key=f"bs:rankings:players:{country_code}:{limit}:{after or 'first'}",
            ttl=settings.RANKINGS_CACHE_TTL,
        )

    async def get_top_players(
        self, country_code: str = "global", n: int = 200, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """
        Pages through /rankings/{cc}/players collecting up to n players.

        n cannot exceed 200 in practice: the rankings API hard-caps every
        response at 200 items, and position cursors past 200 return empty
        pages (verified). page_size must also stay below 200 - exactly when
        limit >= 200 the API omits the paging cursor, which would silently
        end pagination after the first page. At page sizes under 200 it
        supplies proper opaque cursors (base64 {"pos": N}) and pages chain.
        """
        results: list[dict[str, Any]] = []
        after: Optional[str] = None
        while len(results) < n:
            remaining = n - len(results)
            data = await self.get_rankings_players_page(
                country_code, limit=min(page_size, remaining), after=after
            )
            items = data.get("items", [])
            if not items:
                break
            results.extend(items)
            after = (data.get("paging", {}) or {}).get("cursors", {}).get("after")
            if not after:
                break
        return results[:n]
