"""
run.py — Start LandSentinel: Telegram bot + APScheduler together.

Usage:
    py -3.13 run.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# Silence noisy network retry logs
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


async def post_init(app):
    """Called after bot is initialized — start scheduler."""
    import db
    import pipeline
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    import pytz

    db.init_db()
    pipeline._ensure_gee()

    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Kolkata"))

    async def scan_job():
        import db as _db
        due_plots = _db.get_plots_due_for_scan()
        log.info(f"[scheduler] {len(due_plots)} plot(s) due for scan")
        for plot in due_plots:
            try:
                await pipeline.run_scan_for_plot(plot["id"], bot=app.bot)
            except Exception as exc:
                log.error(f"[scheduler] plot {plot['id']} failed: {exc}", exc_info=True)

    scheduler.add_job(
        scan_job,
        trigger=IntervalTrigger(hours=6),
        id="plot_scan_job",
        max_instances=1,
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler

    # Run an initial pass right away
    asyncio.create_task(scan_job())
    log.info("[run] Bot + scheduler running.")


async def post_shutdown(app):
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "your-token":
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

    import db
    from telegram_bot import build_app

    db.init_db()
    app = build_app()
    app.post_init = post_init
    app.post_shutdown = post_shutdown

    log.info("[run] Starting LandSentinel...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        timeout=15,
    )


if __name__ == "__main__":
    main()
