"""
Runs the bot via long polling inside the same container/process as the
FastAPI app - no webhook URL, no SSL certificate, no reverse proxy needed.
Started from main.py's startup event, stopped on shutdown.
"""
import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.bot.handlers import (
    cmd_addtrigger,
    cmd_brawlers,
    cmd_card,
    cmd_help,
    cmd_leaderboard,
    cmd_link,
    cmd_meta,
    cmd_player,
    cmd_rank,
    cmd_rate,
    cmd_removetrigger,
    cmd_start,
    cmd_top1000stats,
    cmd_triggers,
    cmd_unlink,
    on_text_message,
    touch_user_middleware,
)
from app.bot.triggers import seed_default_triggers
from app.config import get_settings
from app.database import async_session

logger = logging.getLogger("bot.runner")

_application: Application | None = None


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # group=-1 runs before every other handler, for every update, so a
    # TelegramUser row always exists by the time a command needs it -
    # see touch_user_middleware in handlers.py.
    application.add_handler(MessageHandler(filters.ALL, touch_user_middleware), group=-1)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("link", cmd_link))
    application.add_handler(CommandHandler("unlink", cmd_unlink))
    application.add_handler(CommandHandler(["player", "stats"], cmd_player))
    application.add_handler(CommandHandler("rate", cmd_rate))
    application.add_handler(CommandHandler("rank", cmd_rank))
    application.add_handler(CommandHandler(["brawlers", "brawler"], cmd_brawlers))
    application.add_handler(CommandHandler("card", cmd_card))
    application.add_handler(CommandHandler(["leaderboard", "top1000"], cmd_leaderboard))
    application.add_handler(CommandHandler("top1000stats", cmd_top1000stats))
    application.add_handler(CommandHandler("meta", cmd_meta))
    application.add_handler(CommandHandler("triggers", cmd_triggers))
    application.add_handler(CommandHandler("addtrigger", cmd_addtrigger))
    application.add_handler(CommandHandler("removetrigger", cmd_removetrigger))

    # Keyword triggers - anything that isn't a /command, checked last.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))

    return application


async def start_bot():
    global _application
    async with async_session() as session:
        await seed_default_triggers(session)
        await session.commit()

    _application = build_application()
    await _application.initialize()
    await _application.start()
    await _application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started (long polling)")


async def stop_bot():
    global _application
    if _application is None:
        return
    await _application.updater.stop()
    await _application.stop()
    await _application.shutdown()
    _application = None
    logger.info("Telegram bot stopped")
