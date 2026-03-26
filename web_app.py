"""
web_app.py — Customer registration web form.

Run: python web_app.py
Opens at: http://localhost:5000

Customer fills in name, email, phone, draws plot on map → saved to DB →
welcome email sent with Telegram connect link.
"""

import json
import logging
import os

from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

import db
import email_sender
from satellite import point_to_polygon, coords_to_polygon

load_dotenv()
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "landsentinel-change-me")

BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("register"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", error=None, form=None)

    # ── Parse form ────────────────────────────────────────────────────────────
    form = request.form
    name       = form.get("name", "").strip()
    email      = form.get("email", "").strip().lower()
    phone      = form.get("phone", "").strip()
    plot_name  = form.get("plot_name", "").strip()
    language   = form.get("language", "en")
    freq       = int(form.get("scan_frequency_days", 5))
    geojson_raw = form.get("geojson", "").strip()
    coords_text = form.get("coords_text", "").strip()

    # Basic validation
    if not name:
        return render_template("register.html", error="Full name is required.", form=form)
    if not email or "@" not in email:
        return render_template("register.html", error="Valid email is required.", form=form)
    if not plot_name:
        return render_template("register.html", error="Plot name is required.", form=form)

    # ── Build polygon ─────────────────────────────────────────────────────────
    geojson = None

    if geojson_raw:
        try:
            geojson = json.loads(geojson_raw)
        except json.JSONDecodeError:
            return render_template("register.html",
                                   error="Invalid map data. Please redraw your plot.",
                                   form=form)

    elif coords_text:
        pairs = _parse_coords_text(coords_text)
        if isinstance(pairs, str):  # error message
            return render_template("register.html", error=pairs, form=form)
        if len(pairs) == 1:
            geojson = point_to_polygon(pairs[0][0], pairs[0][1])
        else:
            geojson = coords_to_polygon(pairs)

    if geojson is None:
        return render_template("register.html",
                               error="Please draw your plot on the map or enter coordinates.",
                               form=form)

    # ── Check for duplicate email ─────────────────────────────────────────────
    if db.get_user_by_email(email):
        return render_template("register.html",
                               error=f"An account with {email} already exists. Contact us if you need help.",
                               form=form)

    # ── Save to database ──────────────────────────────────────────────────────
    db.init_db()
    user_id, link_token = db.create_web_user(name, email, phone, language)
    plot_id = db.create_plot(user_id, plot_name, geojson, scan_frequency_days=freq)

    log.info(f"[web] Registered: {name} <{email}> plot='{plot_name}' (user={user_id} plot={plot_id})")

    # ── Send welcome email ────────────────────────────────────────────────────
    telegram_link = f"https://t.me/{BOT_USERNAME}?start={link_token}" if BOT_USERNAME else None
    try:
        email_sender.send_welcome_email(
            to_email=email,
            customer_name=name,
            plot_name=plot_name,
            link_token=link_token,
        )
    except Exception as exc:
        log.error(f"[web] Welcome email failed: {exc}")
        # Don't block registration if email fails

    return render_template("success.html",
                           name=name,
                           email=email,
                           plot_name=plot_name,
                           freq=freq,
                           telegram_link=telegram_link)


@app.route("/health")
def health():
    return {"status": "ok", "service": "LandSentinel"}, 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_coords_text(text: str) -> list[tuple[float, float]] | str:
    """
    Parse multi-line coordinate text. Returns list of (lat, lon) or error string.
    Accepts:
      16.3067,80.4365
      16.3072 80.4370
      16.3067, 80.4365
    """
    pairs = []
    for i, line in enumerate(text.strip().splitlines(), 1):
        line = line.strip().replace(";", "")
        if not line:
            continue
        # Support comma or space separator
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            return f"Line {i}: could not parse '{line}'. Use format: lat,lon"
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            return f"Line {i}: '{line}' — expected numbers, got text."
        if not (-90 <= lat <= 90):
            return f"Line {i}: latitude {lat} is out of range (-90 to 90)."
        if not (-180 <= lon <= 180):
            return f"Line {i}: longitude {lon} is out of range (-180 to 180)."
        pairs.append((lat, lon))

    if len(pairs) == 0:
        return "No coordinates found. Please enter at least one lat,lon pair."
    return pairs


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    db.init_db()
    port = int(os.getenv("WEB_PORT", 5000))
    print(f"\n[web] LandSentinel registration form running at http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
