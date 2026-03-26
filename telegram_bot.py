"""
telegram_bot.py — LandSentinel Telegram bot (python-telegram-bot v20+ async).

Commands:
  /start          — Register user, welcome message
  /register       — Add a new land plot (name + coordinates)
  /myplots        — List all registered plots
  /scan <name>    — Trigger immediate scan
  /status         — Summary of all plots + alerts
  /report <name>  — Generate & send PDF evidence report
"""

import asyncio
import io
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db
import pipeline
import pdf_report
from satellite import point_to_polygon, coords_to_polygon
from telegram.error import NetworkError, TimedOut, Conflict

load_dotenv()
log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Conversation states
ASK_PLOT_NAME, ASK_PLOT_COORDS = range(2)

# ── Welcome messages ──────────────────────────────────────────────────────────

WELCOME_EN = (
    "🛰 *LandSentinel* — Your land, watched from space.\n\n"
    "Commands:\n"
    "/register — Add a land plot\n"
    "/myplots — View your plots\n"
    "/scan <name> — Check a plot now\n"
    "/status — All plots summary\n"
    "/report <name> — Get PDF evidence report"
)

WELCOME_TE = (
    "🛰 *LandSentinel* — మీ భూమిని అంతరిక్షం నుండి పర్యవేక్షిస్తున్నారు.\n\n"
    "ఆదేశాలు:\n"
    "/register — భూమిని నమోదు చేయండి\n"
    "/myplots — మీ భూముల జాబితా\n"
    "/scan <పేరు> — ఇప్పుడు తనిఖీ చేయండి\n"
    "/status — అన్ని భూముల సారాంశం\n"
    "/report <పేరు> — PDF నివేదిక పొందండి"
)


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    tg_user = update.effective_user
    name = tg_user.full_name if tg_user else "User"

    # Check if this is a deep link from the web registration email
    # e.g. /start abc123-token-from-email
    args = ctx.args
    if args:
        token = args[0].strip()
        linked = db.link_telegram_by_token(token, chat_id)
        if linked:
            plots = db.get_user_plots(linked["id"])
            plot_names = ", ".join(p["name"] for p in plots) if plots else "your registered plot"
            await update.message.reply_text(
                f"*Telegram connected successfully!*\n\n"
                f"Welcome, *{linked['name']}*!\n\n"
                f"You will now receive satellite images and alerts for "
                f"*{plot_names}* directly here.\n\n"
                f"No action needed from you — monitoring is automatic.\n\n"
                f"టెలిగ్రామ్ అనుసంధానం విజయవంతంగా జరిగింది! "
                f"ఉపగ్రహ చిత్రాలు ఇక్కడకు వస్తాయి.",
                parse_mode="Markdown",
            )
            return
        else:
            await update.message.reply_text(
                "Link not recognised or already used. "
                "Please contact support or re-register."
            )
            return

    # Normal /start — register as bot user
    db.upsert_user(chat_id, name=name)
    await update.message.reply_text(
        f"{WELCOME_TE}\n\n{WELCOME_EN}", parse_mode="Markdown"
    )


# ── /register (ConversationHandler) ──────────────────────────────────────────

async def cmd_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📍 Plot registration\n\n"
        "మీ భూమికి ఒక పేరు ఇవ్వండి:\n"
        "Give your plot a name (e.g. 'Nanna Farm'):",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_PLOT_NAME


async def reg_got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["plot_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Good! Plot name: *{ctx.user_data['plot_name']}*\n\n"
        "Now send the GPS coordinates.\n\n"
        "*Option 1* — Share your location pin (Telegram attachment → Location)\n"
        "*Option 2* — Type lat,lon pairs, one per line:\n"
        "```\n16.3067,80.4365\n16.3072,80.4365\n16.3072,80.4370\n16.3067,80.4370\n```\n\n"
        "At least 3 points to draw a polygon. Or just 1 point and I'll create a ~500m square.\n\n"
        "Type /cancel to quit.",
        parse_mode="Markdown",
    )
    return ASK_PLOT_COORDS


async def reg_got_coords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    plot_name = ctx.user_data.get("plot_name", "My Plot")

    # Handle location pin
    if update.message.location:
        loc = update.message.location
        polygon = point_to_polygon(loc.latitude, loc.longitude)
        coord_summary = f"{loc.latitude:.4f}°N, {loc.longitude:.4f}°E"
    else:
        # Parse text coordinates
        text = update.message.text.strip()
        pairs = []
        for line in text.splitlines():
            line = line.strip().replace(" ", "")
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 2:
                await update.message.reply_text(
                    f"❌ Could not parse: `{line}`\nExpected format: `lat,lon`",
                    parse_mode="Markdown",
                )
                return ASK_PLOT_COORDS
            try:
                lat, lon = float(parts[0]), float(parts[1])
                pairs.append((lat, lon))
            except ValueError:
                await update.message.reply_text(
                    f"❌ Invalid numbers in: `{line}`", parse_mode="Markdown"
                )
                return ASK_PLOT_COORDS

        if len(pairs) == 0:
            await update.message.reply_text("No coordinates found. Try again.")
            return ASK_PLOT_COORDS
        elif len(pairs) == 1:
            polygon = point_to_polygon(pairs[0][0], pairs[0][1])
            coord_summary = f"{pairs[0][0]:.4f}°N, {pairs[0][1]:.4f}°E"
        else:
            polygon = coords_to_polygon(pairs)
            coord_summary = f"{len(pairs)} vertices"

    # Save to DB
    user = db.get_user_by_chat_id(chat_id)
    if not user:
        db.upsert_user(chat_id)
        user = db.get_user_by_chat_id(chat_id)

    plot_id = db.create_plot(user["id"], plot_name, polygon)

    await update.message.reply_text(
        f"✅ Plot *{plot_name}* registered!\n"
        f"📍 Location: {coord_summary}\n"
        f"🆔 Plot ID: {plot_id}\n\n"
        f"Running first baseline scan… this may take ~30 seconds.",
        parse_mode="Markdown",
    )

    # Trigger baseline scan in background
    asyncio.create_task(_run_pipeline(chat_id, plot_id, ctx))
    return ConversationHandler.END


async def _run_pipeline(chat_id: str, plot_id: int, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await pipeline.run_scan_for_plot(plot_id, bot=ctx.bot)
    except Exception as exc:
        log.error(f"[bot] Pipeline error for plot {plot_id}: {exc}", exc_info=True)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Scan failed: {exc}\nTry /scan later.",
        )


async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Registration cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── /myplots ──────────────────────────────────────────────────────────────────

async def cmd_myplots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = db.get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text("No account found. Use /start first.")
        return

    plots = db.get_user_plots(user["id"])
    if not plots:
        await update.message.reply_text(
            "No plots registered yet. Use /register to add one."
        )
        return

    lines = ["📋 *Your Plots*\n"]
    for p in plots:
        last = p["last_scan_date"] or "Never scanned"
        baseline = "✅ Baseline set" if p["baseline_ndvi"] else "⏳ Awaiting baseline"
        lines.append(f"• *{p['name']}* (ID:{p['id']})\n  Last scan: {last} | {baseline}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /scan <name> ──────────────────────────────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = ctx.args

    if not args:
        await update.message.reply_text(
            "Usage: /scan <plot name>\nExample: /scan Nanna Farm"
        )
        return

    plot_name = " ".join(args)
    user = db.get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text("No account. Use /start first.")
        return

    plot = db.get_plot_by_name(user["id"], plot_name)
    if not plot:
        await update.message.reply_text(
            f"Plot '{plot_name}' not found. Check /myplots for exact names."
        )
        return

    await update.message.reply_text(
        f"🛰 Scanning *{plot_name}*… fetching real satellite data (~30s).",
        parse_mode="Markdown",
    )
    await pipeline.run_scan_for_plot(plot["id"], bot=ctx.bot)


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = db.get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text("No account. Use /start first.")
        return

    plots = db.get_user_plots(user["id"])
    if not plots:
        await update.message.reply_text("No plots registered. Use /register.")
        return

    lines = [f"📊 *LandSentinel Status* — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"]
    for p in plots:
        scans = db.get_recent_scans(p["id"], limit=1)
        if scans:
            s = scans[0]
            status_icon = "🔴" if s["alert_triggered"] else "🟢"
            alert_info = f" | ⚠️ {s['alert_type']}" if s["alert_triggered"] else ""
            lines.append(
                f"{status_icon} *{p['name']}*\n"
                f"  Last scan: {s['acquisition_date']} | "
                f"NDVI: {s['ndvi_mean']:.3f}{alert_info}"
            )
        else:
            lines.append(f"⏳ *{p['name']}* — No scans yet")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /report <name> ────────────────────────────────────────────────────────────

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = ctx.args

    if not args:
        await update.message.reply_text("Usage: /report <plot name>")
        return

    plot_name = " ".join(args)
    user = db.get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text("No account. Use /start first.")
        return

    plot = db.get_plot_by_name(user["id"], plot_name)
    if not plot:
        await update.message.reply_text(f"Plot '{plot_name}' not found.")
        return

    scans = db.get_recent_scans(plot["id"], limit=1)
    if not scans:
        await update.message.reply_text("No scans yet. Run /scan first.")
        return

    await update.message.reply_text(f"📄 Generating PDF report for *{plot_name}*…", parse_mode="Markdown")

    scan = scans[0]
    pdf_bytes = pdf_report.generate_report(plot, scan)

    await update.message.reply_document(
        document=io.BytesIO(pdf_bytes),
        filename=f"LandSentinel_{plot_name.replace(' ', '_')}_{scan['acquisition_date']}.pdf",
        caption=f"Evidence report for {plot_name} — {scan['acquisition_date']}",
    )


# ── Alert delivery (called by pipeline) ──────────────────────────────────────

async def send_alert(bot, chat_id: str, plot_name: str,
                     baseline_rgb: bytes, current_rgb: bytes,
                     ndvi_png: bytes, baseline_date: str, current_date: str,
                     detection: dict):
    """Send 3-image alert bundle to user."""
    alert_type = detection["alert_type"].replace("_", " ").title()
    media = [
        InputMediaPhoto(
            media=io.BytesIO(baseline_rgb),
            caption=f"BASELINE — {baseline_date}",
        ),
        InputMediaPhoto(
            media=io.BytesIO(current_rgb),
            caption=(
                f"CURRENT — {current_date} ⚠️ {alert_type}\n"
                f"NDVI change: {detection['ndvi_change_pct']:+.1f}%"
            ),
        ),
        InputMediaPhoto(
            media=io.BytesIO(ndvi_png),
            caption=(
                f"NDVI Analysis\n"
                f"Confidence: {detection['confidence']*100:.0f}%\n"
                f"{detection['description_en']}"
            ),
        ),
    ]

    await bot.send_media_group(chat_id=chat_id, media=media)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🚨 *ALERT — {plot_name}*\n\n"
            f"EN: {detection['description_en']}\n\n"
            f"TE: {detection['description_te']}"
        ),
        parse_mode="Markdown",
    )


async def send_all_clear(bot, chat_id: str, plot_name: str,
                         rgb_png: bytes, acquisition_date: str,
                         ndvi_mean: float):
    """Send routine all-clear with satellite image."""
    await bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(rgb_png),
        caption=(
            f"✅ *{plot_name}* — All Clear\n"
            f"📅 {acquisition_date}\n"
            f"🌿 NDVI: {ndvi_mean:.3f}\n\n"
            f"మీ భూమి సురక్షితంగా ఉంది. / Your land looks safe."
        ),
        parse_mode="Markdown",
    )


# ── App builder ───────────────────────────────────────────────────────────────

def build_app() -> Application:
    import httpx
    import ssl as _ssl
    from telegram.request import HTTPXRequest

    # Swap anyio TLS backend for asyncio — fixes WinError 10054 on Windows
    try:
        import httpcore._backends.asyncio as _asyncio_be
        import httpcore._backends.anyio as _anyio_be
        _anyio_be.AnyIOBackend = _asyncio_be.AsyncIOBackend
    except Exception as _e:
        log.warning(f"[bot] Could not patch httpcore backend: {_e}")

    request = HTTPXRequest(
        connection_pool_size=8,
        http_version="1.1",
    )

    db.init_db()
    app = Application.builder().token(TOKEN).request(request).build()

    # Register conversation
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("register", cmd_register)],
        states={
            ASK_PLOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_got_name)],
            ASK_PLOT_COORDS: [
                MessageHandler(filters.LOCATION, reg_got_coords),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_got_coords),
            ],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(reg_conv)
    app.add_handler(CommandHandler("myplots", cmd_myplots))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_error_handler(_network_error_handler)

    return app


async def _network_error_handler(update, ctx):
    """Silently swallow network errors — bot auto-retries."""
    if isinstance(ctx.error, (NetworkError, TimedOut, Conflict)):
        log.debug(f"[bot] Network hiccup (auto-retry): {ctx.error}")
    else:
        log.error(f"[bot] Unhandled error: {ctx.error}", exc_info=ctx.error)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")
    app = build_app()
    print("[bot] Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
