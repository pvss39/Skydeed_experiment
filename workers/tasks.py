"""
workers/tasks.py — Celery background tasks.

Workers run on Railway as a separate process:
    celery -A workers.tasks worker --loglevel=info

Each task pulls a plot from DB, runs the satellite scan pipeline,
stores results, and sends Telegram/email alerts.
"""

import logging
from celery import Celery
from config import REDIS_URL

log = logging.getLogger(__name__)

# ── Celery app ──────────────────────────────────────────────────────────────
# Redis is both the broker (job queue) and backend (result store)
celery_app = Celery(
    "landsentinel",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    # Retry failed tasks up to 3 times with exponential backoff
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


# ── Tasks ───────────────────────────────────────────────────────────────────

@celery_app.task(
    name="scan_plot",
    bind=True,
    max_retries=3,
    default_retry_delay=120,  # 2 minutes between retries
)
def scan_plot_task(self, plot_id: int):
    """
    Run a full satellite scan for one plot.

    Steps:
    1. Load plot + user from DB
    2. Fetch real Sentinel-2 imagery via GEE
    3. Detect changes vs baseline
    4. Save scan record (image URL in R2, not blob in DB)
    5. Send alert via Telegram if change detected
    6. Send email report if alert
    """
    import json
    import db
    from pipeline import run_scan_for_plot_sync

    log.info(f"[worker] Starting scan for plot_id={plot_id}")

    plot = db.get_plot(plot_id)
    if not plot:
        log.error(f"[worker] Plot {plot_id} not found — skipping")
        return {"status": "error", "reason": "plot not found"}

    if not plot["is_active"]:
        log.info(f"[worker] Plot {plot_id} is inactive — skipping")
        return {"status": "skipped", "reason": "inactive"}

    try:
        result = run_scan_for_plot_sync(plot_id)
        log.info(f"[worker] Scan complete for plot_id={plot_id}: {result}")
        return result

    except Exception as exc:
        log.error(f"[worker] Scan failed for plot_id={plot_id}: {exc}")
        # Retry with exponential backoff
        raise self.retry(exc=exc)


@celery_app.task(name="send_weekly_report")
def send_weekly_report_task(user_id: int):
    """
    Send a weekly summary PDF report to a user via email.
    Triggered by the scheduler every Sunday.
    """
    import db
    from email_sender import send_weekly_summary

    user = db.get_user_by_id(user_id)
    if not user or not user.get("email"):
        return {"status": "skipped", "reason": "no email"}

    plots = db.get_user_plots(user_id)
    scans = []
    for plot in plots:
        recent = db.get_recent_scans(plot["id"], limit=7)
        scans.extend(recent)

    send_weekly_summary(user["email"], user["name"], plots, scans)
    log.info(f"[worker] Weekly report sent to user_id={user_id}")
    return {"status": "ok"}


@celery_app.task(name="scan_all_due_plots")
def scan_all_due_plots_task():
    """
    Called by the scheduler every 6 hours.
    Queues scan tasks for every plot that is due for a scan.
    """
    import db
    due_plots = db.get_plots_due_for_scan()
    log.info(f"[worker] {len(due_plots)} plots due for scan")

    for plot in due_plots:
        scan_plot_task.delay(plot["id"])

    return {"queued": len(due_plots)}
