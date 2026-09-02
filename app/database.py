"""
Database layer. PostgreSQL via SQLAlchemy 2.0 async ORM.

Player snapshots (not just current state) are stored so historical trends
and "recent" stats can be computed later. Battlelog entries are stored
individually with a composite dedup hash, since the API only returns the
most recent ~25 battles per call and provides no battle id.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
    JSON, String, UniqueConstraint, func
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Player(Base):
    """Latest known identity for a player. Stats live in PlayerSnapshot."""
    __tablename__ = "players"

    tag: Mapped[str] = mapped_column(String(16), primary_key=True)  # e.g. "#2Y8V0YQ8"
    name: Mapped[str] = mapped_column(String(64))
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    snapshots: Mapped[list["PlayerSnapshot"]] = relationship(back_populates="player", cascade="all, delete-orphan")
    battles: Mapped[list["Battle"]] = relationship(back_populates="player", cascade="all, delete-orphan")


class PlayerSnapshot(Base):
    """One row per (player, fetch time) - the basis for trophy-progress
    history. Cheap to store; retained indefinitely for now."""
    __tablename__ = "player_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_tag: Mapped[str] = mapped_column(ForeignKey("players.tag", ondelete="CASCADE"), index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    name: Mapped[str] = mapped_column(String(64))
    trophies: Mapped[int] = mapped_column(Integer)
    highest_trophies: Mapped[int] = mapped_column(Integer)
    exp_level: Mapped[int] = mapped_column(Integer)
    exp_points: Mapped[int] = mapped_column(Integer)

    solo_victories: Mapped[int] = mapped_column(Integer, default=0)
    duo_victories: Mapped[int] = mapped_column(Integer, default=0)
    trio_victories: Mapped[int] = mapped_column(Integer, default=0)  # "3vs3Victories"

    club_tag: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    club_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    club_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    icon_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Full raw API payload retained verbatim for fields not modeled
    # explicitly. Nothing here is fabricated.
    raw: Mapped[dict] = mapped_column(JSON)

    player: Mapped["Player"] = relationship(back_populates="snapshots")
    brawler_stats: Mapped[list["PlayerBrawlerSnapshot"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_player_snapshots_player_time", "player_tag", "fetched_at"),
    )


class Brawler(Base):
    """Reference table: static brawler metadata from /brawlers endpoint."""
    __tablename__ = "brawlers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # brawler id from API
    name: Mapped[str] = mapped_column(String(32), unique=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlayerBrawlerSnapshot(Base):
    """A player's per-brawler stats at the time of one PlayerSnapshot."""
    __tablename__ = "player_brawler_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("player_snapshots.id", ondelete="CASCADE"), index=True)
    brawler_id: Mapped[int] = mapped_column(ForeignKey("brawlers.id"), index=True)
    brawler_name: Mapped[str] = mapped_column(String(32))

    power: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer)
    trophies: Mapped[int] = mapped_column(Integer)
    highest_trophies: Mapped[int] = mapped_column(Integer)

    gadgets: Mapped[list] = mapped_column(JSON, default=list)
    star_powers: Mapped[list] = mapped_column(JSON, default=list)
    gears: Mapped[list] = mapped_column(JSON, default=list)
    hypercharge_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    snapshot: Mapped["PlayerSnapshot"] = relationship(back_populates="brawler_stats")

    __table_args__ = (
        Index("ix_pbs_snapshot_brawler", "snapshot_id", "brawler_id"),
    )


class Battle(Base):
    """
    One deduplicated battlelog entry. The API gives no battle id, so
    (player_tag, dedup_hash) is the unique key, where dedup_hash is a
    SHA-256 of the battle's time, mode, map, own brawler, trophy change,
    and rank (see ingest.flatten_battle).
    """
    __tablename__ = "battles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_tag: Mapped[str] = mapped_column(ForeignKey("players.tag", ondelete="CASCADE"), index=True)
    battle_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    dedup_hash: Mapped[str] = mapped_column(String(64), index=True)

    mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    map: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    battle_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # ranked/friendly/etc
    result: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # victory/defeat/draw, null for showdown rank-based
    is_win: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, index=True)
    # Precomputed at ingestion time by battle_outcome.resolve_battle_outcome(),
    # shared by player and meta analytics. NULL = unclassifiable battle,
    # excluded from win/loss aggregation.
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trophy_change: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # showdown placement

    brawler_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brawler_name: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    brawler_power: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brawler_trophies: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    star_player_tag: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    is_star_player: Mapped[bool] = mapped_column(Boolean, default=False)

    raw: Mapped[dict] = mapped_column(JSON)

    player: Mapped["Player"] = relationship(back_populates="battles")

    __table_args__ = (
        UniqueConstraint("player_tag", "dedup_hash", name="uq_battle_dedup"),
    )


class LeaderboardSnapshot(Base):
    """One row per (rank, country, fetch time) - the trend-history dump."""
    __tablename__ = "leaderboard_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    country_code: Mapped[str] = mapped_column(String(8), default="global", index=True)

    rank: Mapped[int] = mapped_column(Integer, index=True)
    player_tag: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    trophies: Mapped[int] = mapped_column(Integer)
    club_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    icon_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_leaderboard_fetch_country_rank", "fetched_at", "country_code", "rank"),
    )


class LeaderboardCurrent(Base):
    """
    Live membership table: exactly one row per player currently inside the
    tracked top-N for a given country. Upserted every collection run - this
    is what makes the leaderboard "updatable": rows are added when a player
    enters top-N and deleted when they fall out, rather than growing forever.
    """
    __tablename__ = "leaderboard_current"

    country_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    player_tag: Mapped[str] = mapped_column(String(16), primary_key=True)

    rank: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    trophies: Mapped[int] = mapped_column(Integer)
    club_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    icon_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_leaderboard_current_country_rank", "country_code", "rank"),
    )


class LeaderboardMembershipEvent(Base):
    """
    Audit log of players entering/exiting the tracked top-N. This is the
    "history" of add/remove events, separate from LeaderboardCurrent (live
    state) and LeaderboardSnapshot (full periodic dumps for trend charts).
    """
    __tablename__ = "leaderboard_membership_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    player_tag: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(16))  # "entered" | "exited"
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trophies: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_lb_events_country_time", "country_code", "occurred_at"),
    )


class TelegramUser(Base):
    """
    Saved Telegram identity. This is what lets the bot remember someone
    across messages - shorthand commands like /rate or the "rate" keyword
    trigger use `linked_player_tag` instead of asking for a tag every time.
    """
    __tablename__ = "telegram_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    linked_player_tag: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BotTrigger(Base):
    """
    Plain-word triggers for the Telegram bot (e.g. typing "rate" runs the
    same thing as /rate). Stored in the DB, not hard-coded, so new trigger
    words can be added with a bot command (see /addtrigger) - no redeploy.
    """
    __tablename__ = "bot_triggers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    action: Mapped[str] = mapped_column(String(32))  # key into bot/handlers.py's ACTION_HANDLERS
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def init_db():
    """Create tables if they don't exist. Switch to Alembic migrations once
    the schema stabilizes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
