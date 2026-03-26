"""
pipeline.py — fetch → detect → alert → deliver.
Ties satellite.py, change_detector.py, db.py, and telegram_bot.py together.
"""

import asyncio
import json
import logging
import math
import os

from dotenv import load_dotenv

import db
from satellite import fetch_plot_imagery, init_gee
from change_detector import detect_changes
from storage.r2 import upload_image, scan_image_key, baseline_image_key

load_dotenv()
log = logging.getLogger(__name__)

_gee_ready = False


def _ensure_gee():
    global _gee_ready
    if not _gee_ready:
        init_gee()
        _gee_ready = True


async def run_scan_for_plot(plot_id: int, bot=None):
    """
    Full scan pipeline for one plot.

    1. Load plot from DB
    2. Fetch real imagery (Sentinel-2 or SAR fallback)
    3. No imagery → log + skip
    4. No baseline → save as baseline, send confirmation image
    5. Baseline exists → run change_detector
    6. Alert → send 3-image alert via Telegram
    7. No alert → send all-clear + image
    8. Save scan to DB
    """
    _ensure_gee()

    plot = db.get_plot(plot_id)
    if not plot:
        log.error(f"[pipeline] Plot {plot_id} not found")
        return

    # Resolve Telegram chat_id for this plot's owner
    user = _get_user_for_plot(plot)
    chat_id = user["telegram_chat_id"] if user else os.getenv("TEST_CHAT_ID")

    plot_name = plot["name"]
    geojson = json.loads(plot["geojson_polygon"])

    log.info(f"[pipeline] Scanning plot '{plot_name}' (id={plot_id})")

    # ── Step 2: Fetch imagery ─────────────────────────────────────────────────
    imagery = fetch_plot_imagery(geojson, days_back=20, plot_name=plot_name)

    if imagery is None:
        log.warning(f"[pipeline] No imagery for plot {plot_id} — skipping")
        if bot and chat_id:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🛰 *{plot_name}*\n"
                    "No clear satellite imagery available this pass "
                    "(heavy cloud cover). Will retry next scheduled scan.\n\n"
                    "ఈ సారి ఉపగ్రహ చిత్రం అందుబాటులో లేదు."
                ),
                parse_mode="Markdown",
            )
        return

    rgb_png = imagery["rgb_png"]
    ndvi_png = imagery["ndvi_png"]
    mapbox_png = imagery.get("mapbox_png")  # None in POC mode
    ndvi_mean = imagery["ndvi_mean"]
    ndbi_mean = imagery["ndbi_mean"]
    cloud_pct = imagery["cloud_cover_pct"]
    acq_date = imagery["acquisition_date"]
    source = imagery["source"]

    log.info(
        f"[pipeline] Got {source} image: date={acq_date} "
        f"ndvi={ndvi_mean:.3f} ndbi={ndbi_mean:.3f} cloud={cloud_pct:.1f}%"
    )

    # ── Step 4: No baseline → set it ─────────────────────────────────────────
    if plot["baseline_ndvi"] is None:
        # Upload to R2, store URL (not raw bytes in DB)
        rgb_url  = upload_image(rgb_png,  baseline_image_key(plot_id, "rgb"))
        ndvi_url = upload_image(ndvi_png, baseline_image_key(plot_id, "ndvi"))

        db.set_plot_baseline(plot_id, ndvi_mean, ndbi_mean, rgb_url, acq_date)

        # Save scan record
        db.save_scan(
            plot_id, ndvi_mean, ndbi_mean, cloud_pct,
            rgb_url, ndvi_url,
            alert_triggered=False, alert_type=None,
            acquisition_date=acq_date,
        )

        if bot and chat_id:
            import io as _io
            if mapbox_png:
                # Mapbox mode: send visual first, then NDVI
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=_io.BytesIO(mapbox_png),
                    caption=(
                        f"🛰 *YOUR LAND — {plot_name}* | {acq_date}\n"
                        f"మీ భూమి — బేస్‌లైన్ సెట్ చేయబడింది."
                    ),
                    parse_mode="Markdown",
                )
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=_io.BytesIO(ndvi_png),
                    caption=(
                        f"🌿 *CHANGE ANALYSIS — NDVI {ndvi_mean:.2f}*\n"
                        f"Source: {source} | Baseline recorded."
                    ),
                    parse_mode="Markdown",
                )
            else:
                # POC mode: single GEE RGB image
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=_io.BytesIO(rgb_png),
                    caption=(
                        f"✅ *{plot_name}* — Baseline Set\n"
                        f"📅 {acq_date} | Source: {source}\n"
                        f"🌿 NDVI: {ndvi_mean:.3f}\n\n"
                        f"ప్రారంభ చిత్రం సేవ్ చేయబడింది. భవిష్యత్ స్కాన్‌లు దీనితో పోల్చబడతాయి.\n"
                        f"Baseline saved. Future scans will be compared to this."
                    ),
                    parse_mode="Markdown",
                )
        log.info(f"[pipeline] Baseline set for plot '{plot_name}'")
        return

    # ── Step 5: Detect changes ────────────────────────────────────────────────
    detection = detect_changes(
        current_ndvi=ndvi_mean,
        baseline_ndvi=plot["baseline_ndvi"],
        current_ndbi=ndbi_mean,
        baseline_ndbi=plot["baseline_ndbi"],
    )

    log.info(
        f"[pipeline] Detection: alert={detection['alert']} "
        f"type={detection['alert_type']} conf={detection['confidence']:.2f}"
    )

    # ── Step 8: Save scan ─────────────────────────────────────────────────────
    rgb_url  = upload_image(rgb_png,  scan_image_key(plot_id, acq_date, "rgb"))
    ndvi_url = upload_image(ndvi_png, scan_image_key(plot_id, acq_date, "ndvi"))

    db.save_scan(
        plot_id, ndvi_mean, ndbi_mean, cloud_pct,
        rgb_url, ndvi_url,
        alert_triggered=detection["alert"],
        alert_type=detection["alert_type"],
        acquisition_date=acq_date,
    )
    db.update_plot_last_scan(plot_id, acq_date)

    # ── Step 6/7: Deliver via Telegram + Email ───────────────────────────────
    import telegram_bot as tbot
    import email_sender

    user = _get_user_for_plot(plot)
    telegram_linked = user and user.get("telegram_linked") and chat_id
    has_email = user and user.get("email")

    # Telegram delivery
    if bot and telegram_linked:
        if detection["alert"]:
            baseline_rgb_url = plot.get("baseline_rgb_url")
            if baseline_rgb_url is None:
                await _send_scan_result(
                    bot, chat_id, plot_name, acq_date, ndvi_mean,
                    rgb_png, ndvi_png, mapbox_png, source, alert=False,
                )
            else:
                # Download baseline image from R2 for Telegram delivery
                import httpx as _httpx
                baseline_rgb_bytes = _httpx.get(baseline_rgb_url).content
                await tbot.send_alert(
                    bot=bot,
                    chat_id=chat_id,
                    plot_name=plot_name,
                    baseline_rgb=baseline_rgb_bytes,
                    current_rgb=mapbox_png if mapbox_png else rgb_png,
                    ndvi_png=ndvi_png,
                    baseline_date=plot["baseline_date"],
                    current_date=acq_date,
                    detection=detection,
                )
        else:
            await _send_scan_result(
                bot, chat_id, plot_name, acq_date, ndvi_mean,
                rgb_png, ndvi_png, mapbox_png, source, alert=False,
            )
    else:
        log.info(f"[pipeline] No Telegram for plot {plot_id} — skipping Telegram delivery")

    # Email delivery (always send if email exists)
    if has_email:
        try:
            email_sender.send_scan_email(
                to_email=user["email"],
                customer_name=user["name"] or "Customer",
                plot_name=plot_name,
                rgb_png=rgb_png,
                ndvi_png=ndvi_png,
                acquisition_date=acq_date,
                ndvi_mean=ndvi_mean,
                source=source,
                alert=detection["alert"],
                alert_type=detection.get("alert_type"),
                description_en=detection.get("description_en", ""),
            )
        except Exception as exc:
            log.error(f"[pipeline] Email delivery failed: {exc}", exc_info=True)
    else:
        log.info(f"[pipeline] No email for plot {plot_id} — skipping email delivery")


async def _send_scan_result(bot, chat_id, plot_name, acq_date, ndvi_mean,
                            rgb_png, ndvi_png, mapbox_png, source, alert=False):
    """Send scan images to Telegram — dual-image (Mapbox mode) or single (POC mode)."""
    import io as _io
    import telegram_bot as tbot

    if mapbox_png:
        # Mapbox mode: visual first, NDVI second
        await bot.send_photo(
            chat_id=chat_id,
            photo=_io.BytesIO(mapbox_png),
            caption=f"🛰 *YOUR LAND — {plot_name}* | {acq_date}",
            parse_mode="Markdown",
        )
        await bot.send_photo(
            chat_id=chat_id,
            photo=_io.BytesIO(ndvi_png),
            caption=f"🌿 *CHANGE ANALYSIS — NDVI {ndvi_mean:.2f}*\n✅ No significant changes detected.",
            parse_mode="Markdown",
        )
    else:
        # POC mode: single GEE RGB
        await tbot.send_all_clear(bot, chat_id, plot_name, rgb_png, acq_date, ndvi_mean)


def _get_user_for_plot(plot) -> dict | None:
    return db.get_user_by_id(plot["user_id"])


def run_scan_for_plot_sync(plot_id: int) -> dict:
    """
    Synchronous wrapper around run_scan_for_plot.
    Called by Celery workers (which are synchronous processes).
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_scan_for_plot(plot_id, bot=None))
        return {"status": "ok", "plot_id": plot_id}
    finally:
        loop.close()


# ── CLI usage ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--plot-id", type=int, required=True,
                        help="Plot ID to scan")
    args = parser.parse_args()

    db.init_db()
    asyncio.run(run_scan_for_plot(args.plot_id, bot=None))
