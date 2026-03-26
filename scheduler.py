"""
scheduler.py — APScheduler: checks all active plots every 6 hours,
runs pipeline for any that are due for a scan.
"""

import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import db
import pipeline

load_dotenv()
log = logging.getLogger(__name__)


async def scheduled_scan_job():
    """Scan all plots that are due. Called every 6 hours."""
    due_plots = db.get_plots_due_for_scan()
    log.info(f"[scheduler] {len(due_plots)} plot(s) due for scan")

    for plot in due_plots:
        log.info(f"[scheduler] Scanning plot '{plot['name']}' (id={plot['id']})")
        try:
            await pipeline.run_scan_for_plot(plot["id"], bot=None)
        except Exception as exc:
            log.error(
                f"[scheduler] Failed to scan plot {plot['id']}: {exc}",
                exc_info=True,
            )


def build_scheduler() -> AsyncIOScheduler:
    """Build and return configured scheduler (not yet started)."""
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        scheduled_scan_job,
        trigger="interval",
        hours=6,
        id="plot_scan_job",
        name="Scan due plots",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler


def start_scheduler_standalone():
    """Run only the scheduler (no bot) — useful for testing."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    db.init_db()
    pipeline._ensure_gee()

    loop = asyncio.get_event_loop()
    scheduler = build_scheduler()
    scheduler.start()

    log.info("[scheduler] Started. Next scan in 6 hours.")
    log.info(f"[scheduler] Scheduled jobs: {scheduler.get_jobs()}")

    # Also run once immediately on startup
    loop.run_until_complete(scheduled_scan_job())

    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("[scheduler] Shut down.")


if __name__ == "__main__":
    start_scheduler_standalone()
