"""
email_sender.py — Send satellite images and alerts via Resend API.

Resend is a transactional email API (resend.com).
Free tier: 3000 emails/month — enough for 5000 users at weekly cadence.

Get your API key: resend.com → API Keys → Create API Key
Add to .env: RESEND_API_KEY=re_xxxxxxxxxxxx

Falls back to Gmail SMTP if RESEND_API_KEY is not set (local dev).
"""

import io
import logging
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# Import branding from config (white-label friendly)
try:
    from config import APP_NAME, SUPPORT_EMAIL
    FROM_NAME = APP_NAME
    FROM_EMAIL = SUPPORT_EMAIL
except ImportError:
    FROM_NAME  = os.getenv("EMAIL_FROM_NAME", "LandSentinel")
    FROM_EMAIL = os.getenv("SUPPORT_EMAIL", os.getenv("GMAIL_USER", "noreply@example.com"))

RESEND_API_KEY  = os.getenv("RESEND_API_KEY", "")
GMAIL_USER      = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")
BOT_USERNAME    = os.getenv("TELEGRAM_BOT_USERNAME", "")


def _send_via_resend(to_email: str, subject: str, html: str,
                     attachments: list | None = None) -> bool:
    """
    Send email via Resend API.
    attachments: list of {"filename": str, "content": bytes, "content_type": str}
    Returns True on success.
    """
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        params = {
            "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
            "to":      [to_email],
            "subject": subject,
            "html":    html,
        }
        resend.Emails.send(params)
        return True
    except Exception as exc:
        log.error(f"[email] Resend failed: {exc}")
        return False


def _send_via_gmail(msg: MIMEMultipart, to_email: str):
    """Fallback: send via Gmail SMTP."""
    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("[email] No email credentials configured — skipping")
        return
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
    except Exception as exc:
        log.error(f"[email] Gmail send failed to {to_email}: {exc}", exc_info=True)


def _send_html(to_email: str, subject: str, html: str):
    """Send an HTML email — uses Resend if configured, else Gmail SMTP."""
    if RESEND_API_KEY:
        _send_via_resend(to_email, subject, html)
    else:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{FROM_NAME} <{GMAIL_USER or FROM_EMAIL}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))
        _send_via_gmail(msg, to_email)


# ── Welcome email (sent right after web registration) ─────────────────────────

def send_welcome_email(to_email: str, customer_name: str,
                       plot_name: str, link_token: str):
    """
    Sends welcome + Telegram connect link to a newly registered customer.
    The deep link encodes their token so the bot can identify them on first tap.
    """
    telegram_link = f"https://t.me/{BOT_USERNAME}?start={link_token}" if BOT_USERNAME else None

    telegram_section = ""
    if telegram_link:
        telegram_section = f"""
        <div style="margin:24px 0;text-align:center">
          <a href="{telegram_link}"
             style="background:#229ED9;color:white;padding:14px 28px;
                    border-radius:8px;text-decoration:none;font-size:16px;font-weight:bold">
            Connect Telegram to receive alerts
          </a>
          <p style="color:#888;font-size:12px;margin-top:8px">
            Tap once — all future satellite images will be sent automatically.
          </p>
        </div>
        """
    else:
        telegram_section = """
        <p style="color:#555">
          Satellite images will be delivered to this email address.
        </p>
        """

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
      <div style="background:#1a3a5c;padding:20px;border-radius:8px;text-align:center">
        <h1 style="color:white;margin:0">{FROM_NAME}</h1>
        <p style="color:#a0c4ff;margin:4px 0">Satellite Land Monitoring</p>
      </div>

      <div style="padding:24px 0">
        <h2 style="color:#1a3a5c">Welcome, {customer_name}!</h2>
        <p>Your land plot <strong>"{plot_name}"</strong> has been registered successfully.</p>
        <p>We will send you satellite imagery every time a scan is completed,
           and an <strong>alert</strong> if any change is detected on your land.</p>

        {telegram_section}

        <hr style="border:none;border-top:1px solid #eee;margin:24px 0">

        <h3 style="color:#1a3a5c">What happens next?</h3>
        <ul style="color:#444;line-height:1.8">
          <li>We fetch the latest Sentinel-2 satellite image of your plot</li>
          <li>A baseline is set — future scans compare against this</li>
          <li>If NDVI drops &gt;15% or construction is detected, you get an alert</li>
          <li>Routine scans run every few days automatically</li>
        </ul>
      </div>

      <div style="background:#f5f5f5;padding:16px;border-radius:8px;
                  font-size:12px;color:#888;text-align:center">
        {FROM_NAME} uses Copernicus Sentinel-2 data &copy; ESA via Google Earth Engine.<br>
        This is an automated monitoring service. Contact: {FROM_EMAIL}
      </div>
    </body></html>
    """
    _send_html(to_email, f"{FROM_NAME} - {plot_name} registered", html)
    log.info(f"[email] Welcome email sent to {to_email}")


# ── Satellite image delivery ───────────────────────────────────────────────────

def send_scan_email(to_email: str, customer_name: str, plot_name: str,
                    rgb_png: bytes | str,   # bytes (Telegram) or URL string (web)
                    ndvi_png: bytes | str,
                    acquisition_date: str, ndvi_mean: float,
                    source: str = "Sentinel-2",
                    alert: bool = False, alert_type: str = None,
                    description_en: str = ""):
    """
    Send satellite scan result to customer via email.
    Attaches RGB image + NDVI image inline.
    """
    if alert:
        subject = f"ALERT - {plot_name} - {alert_type.replace('_',' ').title()} detected"
        header_color = "#c0392b"
        header_text = f"ALERT: {alert_type.replace('_', ' ').title()}"
        body_text = f"""
        <p style="background:#fdecea;border-left:4px solid #c0392b;padding:12px;border-radius:4px">
          <strong>Change detected on your land plot.</strong><br>
          {description_en}
        </p>
        """
    else:
        subject = f"Scan complete - {plot_name} - {acquisition_date}"
        header_color = "#1a3a5c"
        header_text = "Routine Scan Complete"
        body_text = f"""
        <p style="background:#eafaf1;border-left:4px solid #27ae60;padding:12px;border-radius:4px">
          <strong>All clear.</strong> No significant changes detected on your plot.
        </p>
        """

    ndvi_str = f"{ndvi_mean:.3f}" if ndvi_mean == ndvi_mean else "N/A"  # NaN check

    # Images can be R2 URLs (str) or raw bytes embedded as data URIs
    import base64
    if isinstance(rgb_png, str):
        rgb_src = rgb_png
    else:
        rgb_src = "data:image/png;base64," + base64.b64encode(rgb_png).decode()
    if isinstance(ndvi_png, str):
        ndvi_src = ndvi_png
    else:
        ndvi_src = "data:image/png;base64," + base64.b64encode(ndvi_png).decode()

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
      <div style="background:{header_color};padding:20px;border-radius:8px;text-align:center">
        <h1 style="color:white;margin:0">{FROM_NAME}</h1>
        <p style="color:#ddd;margin:4px 0">{header_text}</p>
      </div>

      <div style="padding:24px 0">
        <h2 style="color:#1a3a5c">{plot_name}</h2>
        <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
          <tr>
            <td style="padding:8px;background:#f8f8f8;font-weight:bold;width:40%">Acquisition Date</td>
            <td style="padding:8px">{acquisition_date}</td>
          </tr>
          <tr>
            <td style="padding:8px;background:#f8f8f8;font-weight:bold">NDVI Value</td>
            <td style="padding:8px">{ndvi_str}</td>
          </tr>
          <tr>
            <td style="padding:8px;background:#f8f8f8;font-weight:bold">Data Source</td>
            <td style="padding:8px">{source}</td>
          </tr>
        </table>

        {body_text}

        <h3 style="color:#1a3a5c">True Colour Image</h3>
        <img src="{rgb_src}" style="width:100%;border-radius:8px;border:1px solid #ddd">

        <h3 style="color:#1a3a5c">NDVI Analysis (Green = Healthy Vegetation)</h3>
        <img src="{ndvi_src}" style="width:100%;border-radius:8px;border:1px solid #ddd">

        <p style="font-size:12px;color:#888;margin-top:24px">
          NDVI (Normalised Difference Vegetation Index): values near 1.0 = dense healthy vegetation,
          values near 0 or negative = bare soil, water, or built-up area.
        </p>
      </div>

      <div style="background:#f5f5f5;padding:16px;border-radius:8px;
                  font-size:12px;color:#888;text-align:center">
        Sentinel-2 data &copy; ESA / Copernicus Programme via Google Earth Engine.
        Contact: {FROM_EMAIL}
      </div>
    </body></html>
    """

    _send_html(to_email, subject, html)
    log.info(f"[email] Scan email sent to {to_email} (alert={alert})")


# ── Weekly summary email ──────────────────────────────────────────────────────

def send_weekly_summary(to_email: str, customer_name: str,
                        plots: list, scans: list):
    """Send a weekly digest of all plot scans to the user."""
    plots_html = ""
    for plot in plots:
        alerts = [s for s in scans if s["plot_id"] == plot["id"] and s.get("alert_triggered")]
        plots_html += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee">{plot['name']}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{plot.get('last_scan_date','—')}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;color:{'#c0392b' if alerts else '#27ae60'}">
            {'⚠ ' + alerts[0]['alert_type'] if alerts else '✓ Clear'}
          </td>
        </tr>
        """

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
      <div style="background:#1a3a5c;padding:20px;border-radius:8px;text-align:center">
        <h1 style="color:white;margin:0">{FROM_NAME}</h1>
        <p style="color:#a0c4ff;margin:4px 0">Weekly Summary</p>
      </div>
      <div style="padding:24px 0">
        <h2 style="color:#1a3a5c">Hello, {customer_name}!</h2>
        <p>Here is your weekly land monitoring summary:</p>
        <table style="width:100%;border-collapse:collapse">
          <tr style="background:#f8f8f8">
            <th style="padding:10px;text-align:left">Plot</th>
            <th style="padding:10px;text-align:left">Last Scan</th>
            <th style="padding:10px;text-align:left">Status</th>
          </tr>
          {plots_html}
        </table>
      </div>
      <div style="background:#f5f5f5;padding:16px;border-radius:8px;
                  font-size:12px;color:#888;text-align:center">
        {FROM_NAME} — Satellite Land Monitoring. Contact: {FROM_EMAIL}
      </div>
    </body></html>
    """
    _send_html(to_email, f"{FROM_NAME} — Weekly Summary", html)
    log.info(f"[email] Weekly summary sent to {to_email}")


# ── CLI test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if not RESEND_API_KEY and not GMAIL_USER:
        print("Set RESEND_API_KEY (or GMAIL_USER + GMAIL_APP_PASSWORD) in .env first")
        sys.exit(1)

    test_email = input("Send test email to: ").strip()
    send_welcome_email(
        to_email=test_email,
        customer_name="Test Customer",
        plot_name="Test Farm",
        link_token="test-token-123",
    )
    print(f"Welcome email sent to {test_email}")
