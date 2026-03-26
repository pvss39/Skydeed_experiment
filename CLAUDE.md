# CLAUDE.md — LandSentinel: Satellite Land Monitoring System

> Use this file as your starting prompt to rebuild or extend LandSentinel from scratch in any new Claude session.

---

## WHAT YOU ARE BUILDING

**LandSentinel** — a satellite property monitoring system that fetches **real** Sentinel-2 imagery for registered land plots and sends encroachment/change alerts via a Telegram bot.

The system monitors farmland using Google Earth Engine (GEE), detects NDVI/NDBI changes, and delivers satellite images + PDF evidence reports directly to landowners on Telegram — in Telugu and English.

**Pilot target:** 5 users, Andhra Pradesh, India.

---

## ABSOLUTE RULES

- **NEVER generate fake or synthetic satellite images.** Every image sent to users must come from Google Earth Engine (Sentinel-2) or NASA HLS. If imagery is cloudy, fall back to Sentinel-1 SAR. If nothing is available, send a text message: *"No clear imagery this pass."*
- **SQLite only** — no PostgreSQL, no PostGIS, no Docker.
- **Telegram only** — no React dashboard, no WhatsApp. Telegram IS the dashboard for Phase 1.
- **Ship fast** — no overengineering. Get real imagery flowing to Telegram.

---

## WHAT EXISTS ALREADY (at project start)

- GEE authenticated via `earthengine authenticate` (default credentials)
- GEE project: `my-spread-sheet-473920`
- NDVI computation working via GEE Python API
- Telegram bot: `@Skydeeder_bot` (token in `.env`)
- Farm location: Nanna Farm, Krishna district, AP — `16.321966°N, 80.667474°E`
- Python 3.13 required (`py -3.13`) — Python 3.14 has SSL issues with Telegram API on Windows

---

## PROJECT FILE STRUCTURE

```
landsentinel/
├── .env                  # All secrets and config
├── requirements.txt      # Python dependencies
├── db.py                 # SQLite models + helpers (User, Plot, Scan)
├── satellite.py          # GEE fetch: real RGB + NDVI PNGs for a polygon
├── change_detector.py    # NDVI/NDBI comparison, >15% change = alert
├── telegram_bot.py       # Async bot: /start /register /myplots /scan /status /report
├── pipeline.py           # fetch → detect → alert → deliver (main orchestrator)
├── pdf_report.py         # Evidence PDF with satellite image + metadata (fpdf2)
├── scheduler.py          # APScheduler: scan plots every 6 hours
├── run.py                # Entry point: starts bot + scheduler together
└── CLAUDE.md             # This file
```

---

## DATABASE SCHEMA (db.py)

SQLite, WAL mode, foreign keys ON.

**users**
```sql
id, telegram_chat_id TEXT, name TEXT, email TEXT, phone TEXT,
language TEXT DEFAULT 'en', telegram_link_token TEXT UNIQUE,
telegram_linked INTEGER DEFAULT 0, registered_via TEXT DEFAULT 'bot',
created_at TEXT DEFAULT (datetime('now'))
```

**plots**
```sql
id, user_id INTEGER REFERENCES users(id), name TEXT, geojson_polygon TEXT,
baseline_ndvi REAL, baseline_ndbi REAL, baseline_rgb BLOB, baseline_date TEXT,
last_scan_date TEXT, scan_frequency_days INTEGER DEFAULT 5,
is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
```

**scans**
```sql
id, plot_id INTEGER REFERENCES plots(id), ndvi_mean REAL, ndbi_mean REAL,
cloud_cover_pct REAL, rgb_image BLOB, ndvi_image BLOB,
alert_triggered INTEGER DEFAULT 0, alert_type TEXT,
acquisition_date TEXT, created_at TEXT DEFAULT (datetime('now'))
```

---

## SATELLITE FETCH (satellite.py)

```python
def fetch_plot_imagery(geojson_polygon: dict, days_back: int = 15) -> dict | None:
    """
    Returns: {
        rgb_png: bytes,       # Real Sentinel-2 RGB image
        ndvi_png: bytes,      # NDVI colour-mapped image
        ndvi_mean: float,
        ndbi_mean: float,
        cloud_cover_pct: float,
        acquisition_date: str,
        source: str           # "Sentinel-2" or "Sentinel-1-SAR"
    }
    Returns None if no usable imagery found.
    """
```

**GEE collections and bands:**
- Primary: `COPERNICUS/S2_SR_HARMONIZED`, cloud filter `< 30%`
- NDVI: `image.normalizedDifference(["B8", "B4"])`
- NDBI: `image.normalizedDifference(["B11", "B8"])`
- MNDWI: `image.normalizedDifference(["B3", "B11"])`
- Export via `getThumbURL()` — downloads real PNG bytes
- SAR fallback: `COPERNICUS/S1_GRD`

Helper: `point_to_polygon(lat, lon, size_m=500)` — creates a square polygon from a single GPS point.

---

## CHANGE DETECTION (change_detector.py)

```python
def detect_changes(
    current_ndvi: float, baseline_ndvi: float,
    current_ndbi: float = None, baseline_ndbi: float = None,
    threshold: float = 0.15
) -> dict:
    """
    Returns: {
        alert: bool,
        alert_type: "encroachment" | "construction" | "flooding" | "vegetation_loss" | None,
        ndvi_change_pct: float,
        ndbi_change_pct: float | None,
        confidence: float,
        description_en: str,
        description_te: str   # Telugu translation
    }
    Logic:
    - NDVI drop > threshold → vegetation_loss or encroachment
    - NDBI increase > threshold → construction
    - Both NDVI drop AND NDBI increase → encroachment (high confidence)
    """
```

---

## TELEGRAM BOT (telegram_bot.py)

Using `python-telegram-bot` v20+ (async). **Important:** patch httpcore to use asyncio backend on Windows (avoids WinError 10054):

```python
import httpcore._backends.asyncio as _asyncio_be
import httpcore._backends.anyio as _anyio_be
_anyio_be.AnyIOBackend = _asyncio_be.AsyncIOBackend
```

**Commands:**
- `/start` — Register user (save chat_id), show welcome in Telugu + English
- `/register` — Conversation: ask plot name → ask GPS coordinates (lat,lon pairs or Telegram location pin) → store as GeoJSON → trigger first baseline scan
- `/myplots` — List all registered plots with last scan date
- `/scan <name>` — Trigger immediate scan, reply with real satellite image
- `/status` — Summary of all plots + any pending alerts
- `/report <name>` — Generate and send PDF evidence report

**On alert, bot sends 3 photos:**
1. Baseline RGB: `"BASELINE — {date}"`
2. Current RGB: `"CURRENT — {date} ⚠️ {alert_type}"`
3. NDVI overlay with change statistics

All messages bilingual: Telugu + English.

---

## PIPELINE (pipeline.py)

```python
async def run_scan_for_plot(plot_id: int, bot=None):
    # 1. Load plot from DB
    # 2. Fetch real imagery via satellite.py
    # 3. No imagery → log, skip
    # 4. No baseline → save as baseline, send confirmation image to user
    # 5. Baseline exists → run change_detector
    # 6. Alert triggered → send 3-photo alert via Telegram
    # 7. No alert → send "all clear" image, update last_scan_date
    # 8. Save scan record to DB
```

---

## SCHEDULER (scheduler.py / run.py)

APScheduler `AsyncIOScheduler`, runs every 6 hours:
- Query all active plots where `last_scan_date + scan_frequency_days <= today`
- Run `pipeline.run_scan_for_plot()` for each due plot
- Wired into bot via `post_init` callback so it shares the `bot` instance

---

## PDF REPORT (pdf_report.py)

Using `fpdf2`. Single-page evidence report:
- Plot name, GPS coordinates, GeoJSON boundary
- Real satellite RGB image with acquisition date
- NDVI value and change percentage
- Alert type and confidence score
- Generation timestamp
- Disclaimer: *"Generated from Copernicus Sentinel-2 satellite data via Google Earth Engine."*

---

## .env FILE

```env
GEE_PROJECT=my-spread-sheet-473920
GEE_SERVICE_ACCOUNT_EMAIL=
GEE_PRIVATE_KEY_FILE=

TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_BOT_USERNAME=Skydeeder_bot

GMAIL_USER=yourgmail@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

EMAIL_FROM_NAME=LandSentinel
FLASK_SECRET=change-this-to-random-string
WEB_PORT=5000

TEST_PLOT_LAT=16.321966
TEST_PLOT_LON=80.667474
TEST_PLOT_NAME=Nanna Farm
TEST_CHAT_ID=your-telegram-chat-id
```

---

## REQUIREMENTS.TXT

```
earthengine-api>=0.1.380
google-auth>=2.0
python-telegram-bot>=20.7
apscheduler>=3.10
requests>=2.31
Pillow>=10.0
fpdf2>=2.7
numpy>=1.24
python-dotenv>=1.0
pytz
httpx
```

---

## HOW TO RUN

```bash
# Install dependencies under Python 3.13
py -3.13 -m pip install -r requirements.txt

# Authenticate GEE (one-time)
earthengine authenticate

# Start the bot + scheduler
py -3.13 run.py
```

---

## VERIFY CHECKLIST (run in order)

1. `py -3.13 -c "import ee; ee.Initialize(project='my-spread-sheet-473920'); print('GEE OK')"` → auth works
2. `py -3.13 satellite.py` → downloads real PNG of Nanna Farm, saves to disk
3. `py -3.13 test_bot.py` → bot starts, responds to `/start` on Telegram
4. `py -3.13 run.py` → full system: GEE + scheduler + bot all running
5. Send `/register` in Telegram → register Nanna Farm, baseline scan runs
6. Send `/scan Nanna Farm` → receive real satellite image in Telegram

---

## WHAT NOT TO BUILD (Phase 1 limits)

- No React/web dashboard — Telegram is the UI
- No Stripe / payments — free pilot
- No Docker — run directly with `py -3.13 run.py`
- No WhatsApp — Telegram only
- No LangGraph agents — simple threshold logic for now
- No PostgreSQL — SQLite handles 5 pilot users fine

---

## KNOWN WINDOWS QUIRKS

- Use `py -3.13` not `python` — Python 3.14 has SSL TLS incompatibility with Telegram API
- httpcore uses anyio backend by default → patch it to asyncio in `build_app()` to avoid WinError 10054 connection resets
- Only one bot instance can poll at a time — never run two terminals with `run.py` simultaneously
