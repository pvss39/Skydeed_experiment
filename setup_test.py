"""
setup_test.py — Register Pavan's farm and run first scan.

Usage:
    python setup_test.py --chat-id YOUR_CHAT_ID

To find your chat_id:
    1. Message your bot on Telegram (send /start)
    2. Open this URL in browser (replace TOKEN):
       https://api.telegram.org/bot<TOKEN>/getUpdates
    3. Look for "chat": {"id": XXXXXXX} — that number is your chat_id
"""

import argparse
import asyncio
import json
import logging
import sys
import os

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Farm polygon from the 7 GPS corner pins ───────────────────────────────────
# Coordinates from Google Maps links shared by Pavan
# Plot is near Krishna district, AP (16.3219°N, 80.6672°E area)
# Rectangle built from NW corner (16.321966, 80.667198) and SE corner (16.321906, 80.667750)

FARM_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [80.667198, 16.321966],   # NW
        [80.667750, 16.321966],   # NE
        [80.667750, 16.321906],   # SE
        [80.667198, 16.321906],   # SW
        [80.667198, 16.321966],   # close
    ]]
}

PLOT_NAME = os.getenv("TEST_PLOT_NAME", "Nanna Farm")


async def main():
    parser = argparse.ArgumentParser(description="Register test farm and run first scan")
    parser.add_argument(
        "--chat-id", required=True,
        help="Your Telegram chat_id (get it from /getUpdates after messaging the bot)"
    )
    parser.add_argument(
        "--rescan", action="store_true",
        help="Force rescan even if plot already exists"
    )
    args = parser.parse_args()

    chat_id = str(args.chat_id)

    import db
    db.init_db()

    # Check if user already exists
    user = db.get_user_by_chat_id(chat_id)
    if user:
        print(f"[setup] Found existing user: {user['name']} (id={user['id']})")
        user_id = user["id"]
    else:
        print(f"[setup] Creating user for chat_id={chat_id}")
        user_id = db.upsert_user(chat_id, name="Pavan")

    # Check if plot already exists
    plot = db.get_plot_by_name(user_id, PLOT_NAME)
    if plot and not args.rescan:
        print(f"[setup] Plot '{PLOT_NAME}' already registered (id={plot['id']})")
        plot_id = plot["id"]
    else:
        if not plot:
            print(f"[setup] Creating plot '{PLOT_NAME}'...")
            plot_id = db.create_plot(user_id, PLOT_NAME, FARM_POLYGON, scan_frequency_days=5)
            print(f"[setup] Plot created with id={plot_id}")
        else:
            plot_id = plot["id"]
            print(f"[setup] Using existing plot id={plot_id}")

    # Run the scan (this will set baseline on first run)
    print(f"\n[setup] Running scan for plot_id={plot_id}...")
    print("[setup] This fetches real satellite imagery from GEE — takes ~30 seconds\n")

    import pipeline
    await pipeline.run_scan_for_plot(plot_id, bot=None)

    print(f"\n[setup] Done! Check the DB or run the bot to see results.")
    print(f"[setup] To start the full bot: python run.py")
    print(f"[setup] To rescan: python pipeline.py --plot-id {plot_id}")


if __name__ == "__main__":
    asyncio.run(main())
