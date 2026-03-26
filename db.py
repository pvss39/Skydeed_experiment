"""
db.py — Database models and helpers for LandSentinel.
Tables: users, plots, scans

Supports both:
  - SQLite (local dev / Telegram-only pilot)
  - PostgreSQL (Railway production for web dashboard + 5000 users)

Set DATABASE_URL in .env to switch:
  SQLite (default): not set or "sqlite:///landsentinel.db"
  PostgreSQL: "postgresql://user:pass@host/dbname"
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# ── Backend detection ───────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")

DB_PATH = Path(__file__).parent / "landsentinel.db"


def get_conn():
    """Return a DB connection. Works with both SQLite and PostgreSQL."""
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _ph() -> str:
    """Return the correct SQL placeholder: %s for Postgres, ? for SQLite."""
    return "%s" if USE_POSTGRES else "?"


def init_db():
    """Create all tables. Safe to run multiple times (CREATE IF NOT EXISTS)."""
    ph = _ph()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id                  SERIAL PRIMARY KEY,
                    telegram_chat_id    TEXT,
                    name                TEXT,
                    email               TEXT UNIQUE,
                    google_sub          TEXT UNIQUE,
                    picture_url         TEXT,
                    phone               TEXT,
                    language            TEXT DEFAULT 'en',
                    telegram_link_token TEXT UNIQUE,
                    telegram_linked     INTEGER DEFAULT 0,
                    registered_via      TEXT DEFAULT 'bot',
                    subscription_status TEXT DEFAULT 'trial',
                    plan                TEXT DEFAULT 'starter',
                    razorpay_customer_id TEXT,
                    created_at          TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS plots (
                    id                  SERIAL PRIMARY KEY,
                    user_id             INTEGER NOT NULL REFERENCES users(id),
                    name                TEXT NOT NULL,
                    geojson_polygon     TEXT NOT NULL,
                    baseline_ndvi       REAL,
                    baseline_ndbi       REAL,
                    baseline_rgb_url    TEXT,
                    baseline_date       TEXT,
                    last_scan_date      TEXT,
                    scan_frequency_days INTEGER DEFAULT 5,
                    is_active           INTEGER DEFAULT 1,
                    created_at          TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS scans (
                    id               SERIAL PRIMARY KEY,
                    plot_id          INTEGER NOT NULL REFERENCES plots(id),
                    ndvi_mean        REAL,
                    ndbi_mean        REAL,
                    cloud_cover_pct  REAL,
                    rgb_image_url    TEXT,
                    ndvi_image_url   TEXT,
                    alert_triggered  INTEGER DEFAULT 0,
                    alert_type       TEXT,
                    acquisition_date TEXT,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_chat_id TEXT,
                    name TEXT,
                    email TEXT,
                    google_sub TEXT UNIQUE,
                    picture_url TEXT,
                    phone TEXT,
                    language TEXT DEFAULT 'en',
                    telegram_link_token TEXT UNIQUE,
                    telegram_linked INTEGER DEFAULT 0,
                    registered_via TEXT DEFAULT 'bot',
                    subscription_status TEXT DEFAULT 'trial',
                    plan TEXT DEFAULT 'starter',
                    razorpay_customer_id TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS plots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL,
                    geojson_polygon TEXT NOT NULL,
                    baseline_ndvi REAL,
                    baseline_ndbi REAL,
                    baseline_rgb_url TEXT,
                    baseline_date TEXT,
                    last_scan_date TEXT,
                    scan_frequency_days INTEGER DEFAULT 5,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plot_id INTEGER NOT NULL REFERENCES plots(id),
                    ndvi_mean REAL,
                    ndbi_mean REAL,
                    cloud_cover_pct REAL,
                    rgb_image_url TEXT,
                    ndvi_image_url TEXT,
                    alert_triggered INTEGER DEFAULT 0,
                    alert_type TEXT,
                    acquisition_date TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            # Migrate old columns for existing SQLite DBs
            for col, definition in [
                ("google_sub",           "TEXT UNIQUE"),
                ("picture_url",          "TEXT"),
                ("subscription_status",  "TEXT DEFAULT 'trial'"),
                ("plan",                 "TEXT DEFAULT 'starter'"),
                ("razorpay_customer_id", "TEXT"),
                ("telegram_link_token",  "TEXT UNIQUE"),
                ("telegram_linked",      "INTEGER DEFAULT 0"),
                ("registered_via",       "TEXT DEFAULT 'bot'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass
            for col, definition in [
                ("baseline_rgb_url", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE plots ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass
            for col, definition in [
                ("rgb_image_url",  "TEXT"),
                ("ndvi_image_url", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE scans ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass

    backend = "PostgreSQL" if USE_POSTGRES else f"SQLite at {DB_PATH}"
    print(f"[db] Initialized database — {backend}")


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> dict | None:
    """Fetch a user by primary key. Used by JWT auth in FastAPI routes."""
    ph = _ph()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM users WHERE id = {ph}", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        else:
            row = conn.execute(
                f"SELECT * FROM users WHERE id = {ph}", (user_id,)
            ).fetchone()
            return dict(row) if row else None


def upsert_web_user(
    google_sub: str,
    email: str,
    name: str,
    picture_url: str = "",
) -> dict:
    """
    Create or update a user who logged in via Google OAuth.
    Returns the full user dict.

    Called from auth.py after Google callback — stores Google's unique sub ID
    so the same user isn't duplicated if they change their name/picture.
    """
    ph = _ph()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO users (google_sub, email, name, picture_url, registered_via)
                VALUES ({ph}, {ph}, {ph}, {ph}, 'google')
                ON CONFLICT (google_sub) DO UPDATE SET
                    email       = EXCLUDED.email,
                    name        = EXCLUDED.name,
                    picture_url = EXCLUDED.picture_url
                RETURNING *
            """, (google_sub, email, name, picture_url))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
        else:
            # SQLite: manual upsert
            existing = conn.execute(
                f"SELECT * FROM users WHERE google_sub = {ph}", (google_sub,)
            ).fetchone()
            if existing:
                conn.execute(
                    f"UPDATE users SET email={ph}, name={ph}, picture_url={ph} WHERE google_sub={ph}",
                    (email, name, picture_url, google_sub),
                )
                row = conn.execute(
                    f"SELECT * FROM users WHERE google_sub = {ph}", (google_sub,)
                ).fetchone()
            else:
                cur = conn.execute(
                    f"""INSERT INTO users (google_sub, email, name, picture_url, registered_via)
                        VALUES ({ph}, {ph}, {ph}, {ph}, 'google')""",
                    (google_sub, email, name, picture_url),
                )
                row = conn.execute(
                    f"SELECT * FROM users WHERE id = {ph}", (cur.lastrowid,)
                ).fetchone()
            return dict(row)


def upsert_user(chat_id: str, name: str = None, language: str = "en") -> int:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE telegram_chat_id = ?", (str(chat_id),)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET name=COALESCE(?,name), language=? WHERE telegram_chat_id=?",
                (name, language, str(chat_id)),
            )
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO users (telegram_chat_id, name, language, telegram_linked, registered_via)
                   VALUES (?, ?, ?, 1, 'bot')""",
                (str(chat_id), name, language),
            )
            return cur.lastrowid


def get_user_by_chat_id(chat_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_chat_id = ?", (str(chat_id),)
        ).fetchone()


def create_web_user(name: str, email: str, phone: str,
                    language: str = "en") -> tuple[int, str]:
    """Create a user from web form registration. Returns (user_id, link_token)."""
    import uuid
    token = str(uuid.uuid4())
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (name, email, phone, language,
               telegram_link_token, telegram_linked, registered_via)
               VALUES (?, ?, ?, ?, ?, 0, 'web')""",
            (name, email, phone, language, token),
        )
        return cur.lastrowid, token


def link_telegram_by_token(token: str, chat_id: str) -> sqlite3.Row | None:
    """Called when customer taps the Telegram connect link. Links chat_id to their account."""
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_link_token = ?", (token,)
        ).fetchone()
        if not user:
            return None
        conn.execute(
            """UPDATE users SET telegram_chat_id=?, telegram_linked=1
               WHERE telegram_link_token=?""",
            (str(chat_id), token),
        )
        return conn.execute(
            "SELECT * FROM users WHERE telegram_link_token=?", (token,)
        ).fetchone()


def get_user_by_token(token: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_link_token = ?", (token,)
        ).fetchone()


def get_user_by_email(email: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()


# ── Plot helpers ──────────────────────────────────────────────────────────────

def create_plot(user_id: int, name: str, geojson_polygon: dict,
                scan_frequency_days: int = 5) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO plots (user_id, name, geojson_polygon, scan_frequency_days)
               VALUES (?, ?, ?, ?)""",
            (user_id, name, json.dumps(geojson_polygon), scan_frequency_days),
        )
        return cur.lastrowid


def get_plot(plot_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM plots WHERE id = ?", (plot_id,)).fetchone()


def get_plot_by_name(user_id: int, name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM plots WHERE user_id = ? AND name = ? AND is_active = 1",
            (user_id, name),
        ).fetchone()


def get_user_plots(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM plots WHERE user_id = ? AND is_active = 1 ORDER BY created_at",
            (user_id,),
        ).fetchall()


def get_plots_due_for_scan() -> list[sqlite3.Row]:
    """Return active plots where last_scan_date + frequency <= today (or never scanned)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM plots WHERE is_active = 1 AND (
                last_scan_date IS NULL OR
                date(last_scan_date, '+' || scan_frequency_days || ' days') <= date('now')
            )"""
        ).fetchall()


def set_plot_baseline(plot_id: int, ndvi: float, ndbi: float,
                      rgb_url: str, date_str: str):
    """Store baseline values. rgb_url is a Cloudflare R2 public URL."""
    ph = _ph()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"""UPDATE plots SET baseline_ndvi={ph}, baseline_ndbi={ph},
                    baseline_rgb_url={ph}, baseline_date={ph}, last_scan_date={ph}
                    WHERE id={ph}""",
                (ndvi, ndbi, rgb_url, date_str, date_str, plot_id),
            )
            conn.commit()
        else:
            conn.execute(
                f"""UPDATE plots SET baseline_ndvi={ph}, baseline_ndbi={ph},
                    baseline_rgb_url={ph}, baseline_date={ph}, last_scan_date={ph}
                    WHERE id={ph}""",
                (ndvi, ndbi, rgb_url, date_str, date_str, plot_id),
            )


def update_plot_last_scan(plot_id: int, date_str: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE plots SET last_scan_date=? WHERE id=?", (date_str, plot_id)
        )


# ── Scan helpers ──────────────────────────────────────────────────────────────

def save_scan(
    plot_id: int,
    ndvi_mean: float,
    ndbi_mean: float,
    cloud_cover_pct: float,
    rgb_image_url: str,
    ndvi_image_url: str,
    alert_triggered: bool,
    alert_type: str | None,
    acquisition_date: str,
) -> int:
    """Save a scan record. Images are stored as R2 URLs, not raw bytes."""
    ph = _ph()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO scans
                    (plot_id, ndvi_mean, ndbi_mean, cloud_cover_pct,
                     rgb_image_url, ndvi_image_url,
                     alert_triggered, alert_type, acquisition_date)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})
                RETURNING id
            """, (plot_id, ndvi_mean, ndbi_mean, cloud_cover_pct,
                  rgb_image_url, ndvi_image_url,
                  int(alert_triggered), alert_type, acquisition_date))
            row = cur.fetchone()
            conn.commit()
            return row["id"]
        else:
            cur = conn.execute(
                f"""INSERT INTO scans
                    (plot_id, ndvi_mean, ndbi_mean, cloud_cover_pct,
                     rgb_image_url, ndvi_image_url,
                     alert_triggered, alert_type, acquisition_date)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (plot_id, ndvi_mean, ndbi_mean, cloud_cover_pct,
                 rgb_image_url, ndvi_image_url,
                 int(alert_triggered), alert_type, acquisition_date),
            )
            return cur.lastrowid


def get_recent_scans(plot_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM scans WHERE plot_id=? ORDER BY created_at DESC LIMIT ?",
            (plot_id, limit),
        ).fetchall()


if __name__ == "__main__":
    init_db()
