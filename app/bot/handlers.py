"""
Bot command handlers and keyword-trigger dispatch. Each handler is wrapped
"""
import functools
import logging
import re
from html import escape

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from app.api_client import BrawlStarsAPIError, BrawlStarsClient, PlayerNotFoundError, normalize_tag
from app.bot.formatting import (
    format_brawlers_list,
    format_leaderboard_list,
    format_meta_summary,
    format_player_summary,
    format_rank_summary,
    format_rate_summary,
    format_top1000_stats,
)
from app.bot.triggers import find_triggered_action, get_triggers, invalidate_cache
from app.card.generator import generate_player_card
from app.card.schema import build_card_data
from app.card.theme import CARD_SIZES
from app.config import get_settings
from app.database import BotTrigger, TelegramUser, async_session
from app.meta import classify_meta_tiers, get_brawler_meta
from app.redis_client import get_redis
from app.services.player_service import fetch_ingest_analyze, get_player_rank
from app.leaderboard import get_top1000_stats, get_top_fresh

logger = logging.getLogger("bot.handlers")


def safe_handler(func):
    """Wraps a handler so a bad tag / API hiccup / bug replies with a
    friendly message instead of the bot silently dying on that update."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await func(update, context)
        except PlayerNotFoundError:
            await update.message.reply_text("Couldn't find that player - check the tag eg; #k155m3:3")
        except BrawlStarsAPIError as exc:
            logger.warning(f"Brawl Stars API error in bot handler: {exc}")
            await update.message.reply_text("Brawl Stars API had an issue, needs to fix I suppose.")
        except Exception:
            logger.exception("Unhandled error in bot handler")
            await update.message.reply_text("Something went wrong on my end. Try again in a bit... or blame the dev")
    return wrapper


async def _resolve_tag(update: Update, args: list[str]) -> str | None:
    """Explicit tag argument wins; otherwise falls back to the user's
    /link'd tag (this is the "saved id" payoff - no need to retype it)."""
    if args:
        return normalize_tag(args[0])
    async with async_session() as session:
        user = await session.get(TelegramUser, update.effective_user.id)
        return user.linked_player_tag if user else None


def _is_owner(update: Update) -> bool:
    owner_id = get_settings().TELEGRAM_BOT_OWNER_ID
    return owner_id is not None and update.effective_user.id == owner_id


# ---------------------------------------------------------------------
# User identity middleware - runs before every other handler (see
# runner.py, group=-1) so TelegramUser always exists by the time a
# command handler needs it.
# ---------------------------------------------------------------------
async def touch_user_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if user is None or user.is_bot:
        return
    async with async_session() as session:
        existing = await session.get(TelegramUser, user.id)
        if existing:
            existing.chat_id = chat.id if chat else existing.chat_id
            existing.username = user.username
            existing.first_name = user.first_name
        else:
            session.add(TelegramUser(
                telegram_user_id=user.id,
                chat_id=chat.id if chat else user.id,
                username=user.username,
                first_name=user.first_name,
            ))
        await session.commit()


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------
@safe_handler
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to my  territory!\n\n"
        "/link <tag> - save your player tag so you never have to repeat it\n"
        "/player - your stats (or /player <tag> for anyone else)\n"
        "/rate - your win rate from recent battles\n"
        "/rank - global leaderboard rank (still buggy)\n"
        "/brawlers - your top brawlers (this one too)\n"
        "/card - your graphical player card (i'll change it soon :3)\n"
        "/leaderboard [country] - top players of any country\n"
        "/help - full command + keyword list\n\n"
        "In a DM(for now) you can also just type a word like 'rate' or 'brawlers' "
        "instead of the slash command."
    )


@safe_handler
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with async_session() as session:
        triggers = await get_triggers(session)
    trigger_words = ", ".join(sorted(triggers.keys())) or "none configured"
    text = (
        "<b>Commands</b>\n"
        "/link &lt;tag&gt; - link your player tag\n"
        "/unlink - Breaking up :(\n"
        "/player [tag] - full stats summary\n"
        "/rate [tag] - win rate from recent battles\n"
        "/rank [tag] - global + local rank\n"
        "/brawlers [tag] - top brawlers\n"
        "/card [tag] [variant] - graphical card (variant: telegram/square/desktop)\n"
        "/leaderboard [country] [limit] - top players; any 2-letter country code works (e.g. /leaderboard ir 20)\n"
        "/top1000stats [country] - leaderboard aggregate stats\n"
        "/meta - brawler meta info(idk bleh)\n\n"
        f"<b>Keyword triggers</b> (type these directly in a DM, no slash needed):\n{escape(trigger_words)}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


@safe_handler
async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /link #YOURTAG")
        return
    tag = normalize_tag(args[0])
    result = await fetch_ingest_analyze(tag)  # also validates the tag exists

    async with async_session() as session:
        user = await session.get(TelegramUser, update.effective_user.id)
        if user:
            user.linked_player_tag = tag
        else:
            session.add(TelegramUser(
                telegram_user_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                username=update.effective_user.username,
                first_name=update.effective_user.first_name,
                linked_player_tag=tag,
            ))
        await session.commit()

    await update.message.reply_text(
        f"✅ Linked to {result['player']['name']} ({tag}). "
        f"/rate, /player, /brawlers and /card now work without a tag."
    )


@safe_handler
async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with async_session() as session:
        user = await session.get(TelegramUser, update.effective_user.id)
        if user:
            user.linked_player_tag = None
            await session.commit()
    await update.message.reply_text("Nooooooooo :(. Use /link <tag> to link againnnn")


@safe_handler
async def cmd_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tag = await _resolve_tag(update, context.args or [])
    if not tag:
        await update.message.reply_text("No tag given and nothing linked yet. Use /player <tag> or /link <tag> first.")
        return
    result = await fetch_ingest_analyze(tag)
    await update.message.reply_text(
        format_player_summary(result["player"], result["analytics"], result.get("rank")),
        parse_mode="HTML",
    )


@safe_handler
async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tag = await _resolve_tag(update, context.args or [])
    if not tag:
        await update.message.reply_text("No tag given and nothing linked yet. Use /rate <tag> or /link <tag> first.")
        return
    result = await fetch_ingest_analyze(tag)
    await update.message.reply_text(format_rate_summary(result["player"], result["analytics"]), parse_mode="HTML")


@safe_handler
async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tag = await _resolve_tag(update, context.args or [])
    if not tag:
        await update.message.reply_text("No tag given and nothing linked yet. Use /rank <tag> or /link <tag> first.")
        return
    rank_info = await get_player_rank(tag)
    await update.message.reply_text(format_rank_summary(rank_info), parse_mode="HTML")


@safe_handler
async def cmd_brawlers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tag = await _resolve_tag(update, context.args or [])
    if not tag:
        await update.message.reply_text("No tag given and nothing linked yet. Use /brawlers <tag> or /link <tag> first.")
        return
    result = await fetch_ingest_analyze(tag)
    await update.message.reply_text(format_brawlers_list(result["player"]), parse_mode="HTML")


@safe_handler
async def cmd_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = list(context.args or [])
    variant = "telegram"
    if args and args[-1].lower() in CARD_SIZES:
        variant = args.pop().lower()

    tag = await _resolve_tag(update, args)
    if not tag:
        await update.message.reply_text("No tag given and nothing linked yet. Use /card <tag> or /link <tag> first.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
    result = await fetch_ingest_analyze(tag)
    card_data = build_card_data(result["player"], result["analytics"])
    png_bytes = await generate_player_card(card_data, variant)
    await update.message.reply_photo(photo=png_bytes, caption=f"{result['player']['name']} · {tag}")


def _parse_country_args(args: list[str]) -> tuple[str, int]:
    """
    Shared argument parsing for /leaderboard and /top1000stats:
      - first arg: 'global' (the default) or a 2-letter country code,
        stored lowercase to match the API's path convention
      - optional second arg: how many rows to show (clamped to 1-200)
    Raises ValueError on a malformed country code so the caller can show a
    usage line without ever hitting the API.
    """
    country = "global"
    if args:
        candidate = args[0].strip().lower()
        if candidate != "global" and not re.fullmatch(r"[a-z]{2}", candidate):
            raise ValueError(candidate)
        country = candidate
    limit = 10
    if len(args) > 1:
        try:
            limit = max(1, min(int(args[1]), 200))
        except ValueError:
            pass
    return country, limit


def _country_display(country: str) -> str:
    return country.upper() if country != "global" else "Global"


@safe_handler
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    try:
        country, limit = _parse_country_args(args)
    except ValueError:
        await update.message.reply_text(
            "Usage: /leaderboard [country] [limit] - country is 'global' or a "
            "2-letter code, e.g. /leaderboard ir 20"
        )
        return
    redis = await get_redis()
    client = BrawlStarsClient(redis)
    try:
        async with async_session() as session:
            # Works for ANY country: if the scheduler doesn't track this one,
            # get_top_fresh collects its visible top-200 on the spot.
            players = await get_top_fresh(
                session, client, country, limit, get_settings().LEADERBOARD_TOP_N
            )
    except BrawlStarsAPIError as exc:
        if exc.status_code == 404:
            await update.message.reply_text(
                f"No leaderboard found for '{_country_display(country)}' - check "
                "the 2-letter country code (e.g. de, us, sa, tr)."
            )
            return
        raise
    finally:
        await client.aclose()
    if not players:
        # The rankings endpoint answers 200 with an EMPTY list for unknown
        # country codes (verified), so an empty result here almost always
        # means a typo'd code rather than an API hiccup.
        await update.message.reply_text(
            f"No leaderboard found for '{_country_display(country)}' - check the "
            "2-letter country code (e.g. de, us, sa, tr). If the code is right, "
            "the API may be hiccuping - try again in a moment."
        )
        return
    await update.message.reply_text(
        format_leaderboard_list(_country_display(country), players, limit), parse_mode="HTML"
    )


@safe_handler
async def cmd_top1000stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    try:
        country, _ = _parse_country_args(args)
    except ValueError:
        await update.message.reply_text(
            "Usage: /top1000stats [country] - country is 'global' or a 2-letter code."
        )
        return
    redis = await get_redis()
    client = BrawlStarsClient(redis)
    try:
        async with async_session() as session:
            # Same on-demand fallback: make sure the pool exists/is fresh first.
            await get_top_fresh(session, client, country, limit=1, top_n=get_settings().LEADERBOARD_TOP_N)
            stats = await get_top1000_stats(session, country)
    except BrawlStarsAPIError as exc:
        if exc.status_code == 404:
            await update.message.reply_text(
                f"No leaderboard found for '{_country_display(country)}' - check "
                "the 2-letter country code (e.g. de, us, sa, tr)."
            )
            return
        raise
    finally:
        await client.aclose()
    if stats.get("count", 0) == 0:
        await update.message.reply_text(
            f"No leaderboard found for '{_country_display(country)}' - check the "
            "2-letter country code (e.g. de, us, sa, tr). If the code is right, "
            "the API may be hiccuping - try again in a moment."
        )
        return
    await update.message.reply_text(
        format_top1000_stats(_country_display(country), stats), parse_mode="HTML"
    )


@safe_handler
async def cmd_meta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with async_session() as session:
        brawlers = await get_brawler_meta(session)
    tiers = classify_meta_tiers(brawlers)
    await update.message.reply_text(format_meta_summary(tiers), parse_mode="HTML")


# ---------------------------------------------------------------------
# Owner-only trigger management - lets the bot owner add/remove keyword
# triggers from inside Telegram, no redeploy or DB access needed.
# ---------------------------------------------------------------------
@safe_handler
async def cmd_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("Owner-only command.")
        return
    async with async_session() as session:
        result = await session.execute(select(BotTrigger).order_by(BotTrigger.keyword))
        rows = result.scalars().all()
    if not rows:
        await update.message.reply_text("No triggers configured.")
        return
    lines = [f"{'✅' if r.enabled else '⛔'} {r.keyword} → {r.action}" for r in rows]
    await update.message.reply_text("\n".join(lines))


@safe_handler
async def cmd_addtrigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("Owner-only command.")
        return
    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text(
            f"Usage: /addtrigger <keyword> <action>\nValid actions: {', '.join(ACTION_HANDLERS)}"
        )
        return
    keyword, action = args[0].lower(), args[1].lower()
    if action not in ACTION_HANDLERS:
        await update.message.reply_text(f"Unknown action '{action}'. Valid actions: {', '.join(ACTION_HANDLERS)}")
        return
    async with async_session() as session:
        await session.execute(
            pg_insert(BotTrigger).values(keyword=keyword, action=action)
            .on_conflict_do_update(index_elements=["keyword"], set_={"action": action, "enabled": True})
        )
        await session.commit()
    invalidate_cache()
    await update.message.reply_text(f"✅ '{keyword}' now triggers '{action}'.")


@safe_handler
async def cmd_removetrigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("Owner-only command.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /removetrigger <keyword>")
        return
    keyword = args[0].lower()
    async with async_session() as session:
        result = await session.execute(select(BotTrigger).where(BotTrigger.keyword == keyword))
        row = result.scalar_one_or_none()
        if not row:
            await update.message.reply_text(f"No trigger '{keyword}' found.")
            return
        await session.delete(row)
        await session.commit()
    invalidate_cache()
    await update.message.reply_text(f"🗑 Removed trigger '{keyword}'.")


# ---------------------------------------------------------------------
# Keyword-trigger dispatch - what makes typing "meta" or "rate" work
# ---------------------------------------------------------------------
ACTION_HANDLERS = {
    "player": cmd_player,
    "rate": cmd_rate,
    "rank": cmd_rank,
    "brawlers": cmd_brawlers,
    "leaderboard": cmd_leaderboard,
    "card": cmd_card,
    "meta": cmd_meta,
    "help": cmd_help,
}


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat = update.effective_chat
    if chat.type != "private":
        # In groups, only react when directly addressed - replying to the
        # bot's own message, or @mentioning its username - so it doesn't
        # answer every time someone types a common word like "help".
        addressed = bool(
            (message.reply_to_message and message.reply_to_message.from_user
             and message.reply_to_message.from_user.id == context.bot.id)
        )
        bot_username = context.bot.username
        if bot_username and f"@{bot_username.lower()}" in message.text.lower():
            addressed = True
        if not addressed:
            return

    async with async_session() as session:
        triggers = await get_triggers(session)
    action = find_triggered_action(message.text, triggers)
    if not action:
        return

    handler = ACTION_HANDLERS.get(action)
    if handler:
        await handler(update, context)
