"""
config.py — Single source of truth for branding and app configuration.

WHITE LABELING:
    Change APP_NAME in .env to rebrand the entire product.
    Every file imports from here. Nothing is hardcoded anywhere.

    Default brand: SkyDeed
    To rebrand:    Set APP_NAME=KisanEye in .env → done.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Brand (White Label) ────────────────────────────────────────────────────────
APP_NAME        = os.getenv("APP_NAME",        "SkyDeed")
APP_TAGLINE     = os.getenv("APP_TAGLINE",     "Satellite Land Monitoring")
APP_DOMAIN      = os.getenv("APP_DOMAIN",      "skydeed.in")
SUPPORT_EMAIL   = os.getenv("SUPPORT_EMAIL",   "support@skydeed.in")
APP_COLOR       = os.getenv("APP_COLOR",       "#2D6A4F")   # primary brand colour
APP_LOGO_URL    = os.getenv("APP_LOGO_URL",    "")          # optional logo URL

# ── Database ───────────────────────────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL",    "")          # PostgreSQL on Railway
SQLITE_PATH     = os.getenv("SQLITE_PATH",     "landsentinel.db")  # local dev fallback

# ── Redis ──────────────────────────────────────────────────────────────────────
REDIS_URL       = os.getenv("REDIS_URL",       "redis://localhost:6379/0")

# ── Cloudflare R2 (image storage) ─────────────────────────────────────────────
R2_ACCOUNT_ID   = os.getenv("R2_ACCOUNT_ID",   "")
R2_ACCESS_KEY   = os.getenv("R2_ACCESS_KEY",   "")
R2_SECRET_KEY   = os.getenv("R2_SECRET_KEY",   "")
R2_BUCKET_NAME  = os.getenv("R2_BUCKET_NAME",  "skydeed-images")
R2_PUBLIC_URL   = os.getenv("R2_PUBLIC_URL",   "")         # e.g. https://pub-xxx.r2.dev

# ── Google Earth Engine ────────────────────────────────────────────────────────
GEE_PROJECT     = os.getenv("GEE_PROJECT",     "my-spread-sheet-473920")
GEE_SERVICE_ACCOUNT_EMAIL = os.getenv("GEE_SERVICE_ACCOUNT_EMAIL", "")
GEE_PRIVATE_KEY_FILE      = os.getenv("GEE_PRIVATE_KEY_FILE",      "")

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN",    "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")

# ── Google OAuth (SSO) ─────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID      = os.getenv("GOOGLE_CLIENT_ID",      "")
GOOGLE_CLIENT_SECRET  = os.getenv("GOOGLE_CLIENT_SECRET",  "")
GOOGLE_REDIRECT_URI   = os.getenv("GOOGLE_REDIRECT_URI",   "http://localhost:8000/auth/google/callback")

# ── JWT Auth ───────────────────────────────────────────────────────────────────
JWT_SECRET      = os.getenv("JWT_SECRET",      "change-this-to-random-64-char-string")
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "30"))

# ── Razorpay (payments) ────────────────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID",     "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# ── Resend (email) ─────────────────────────────────────────────────────────────
RESEND_API_KEY  = os.getenv("RESEND_API_KEY",  "")

# ── Mapbox ─────────────────────────────────────────────────────────────────────
ENABLE_MAPBOX   = os.getenv("ENABLE_MAPBOX",   "false").strip().lower() == "true"
MAPBOX_TOKEN    = os.getenv("MAPBOX_TOKEN",    "")

# ── Flask / Web ────────────────────────────────────────────────────────────────
FLASK_SECRET    = os.getenv("FLASK_SECRET",    "change-this-to-a-random-string")
WEB_PORT        = int(os.getenv("WEB_PORT",    "8000"))

# ── Test plot (dev only) ───────────────────────────────────────────────────────
TEST_PLOT_LAT   = float(os.getenv("TEST_PLOT_LAT", "16.321966"))
TEST_PLOT_LON   = float(os.getenv("TEST_PLOT_LON", "80.667474"))
TEST_PLOT_NAME  = os.getenv("TEST_PLOT_NAME",  "Nanna Farm")
TEST_CHAT_ID    = os.getenv("TEST_CHAT_ID",    "")

# ── Subscription plans ─────────────────────────────────────────────────────────
PLANS = {
    "starter": {
        "name":        f"{APP_NAME} Starter",
        "price_inr":   499,
        "plots":       1,
        "scan_days":   7,
        "description": "1 plot, weekly scans",
    },
    "farmer": {
        "name":        f"{APP_NAME} Farmer",
        "price_inr":   999,
        "plots":       3,
        "scan_days":   5,
        "description": "3 plots, every 5 days",
    },
    "pro": {
        "name":        f"{APP_NAME} Pro",
        "price_inr":   2499,
        "plots":       10,
        "scan_days":   3,
        "description": "10 plots, every 3 days",
    },
}
