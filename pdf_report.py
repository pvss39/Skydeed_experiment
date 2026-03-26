"""
pdf_report.py — Generate a single-page evidence PDF using fpdf2.
Contains: plot metadata, satellite image, NDVI stats, alert type, timestamp.
"""

import io
import json
import math
from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos


DISCLAIMER = (
    "This report is generated from Copernicus Sentinel-2 satellite data "
    "provided via Google Earth Engine. Sentinel-2 imagery is © European Space Agency (ESA). "
    "Analysis by LandSentinel - for informational purposes only."
)


def generate_report(plot, scan) -> bytes:
    """
    Build a PDF evidence report.

    Args:
        plot : sqlite3.Row  (from db.plots)
        scan : sqlite3.Row  (from db.scans)

    Returns:
        bytes — PDF file content
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Header ────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 80, 160)
    pdf.cell(0, 12, "LandSentinel - Evidence Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ── Plot details ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Plot Details", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)

    geojson = json.loads(plot["geojson_polygon"])
    coord_str = _summarise_polygon(geojson)

    rows = [
        ("Plot Name", plot["name"]),
        ("Plot ID", str(plot["id"])),
        ("Coordinates", coord_str),
        ("Scan Frequency", f"Every {plot['scan_frequency_days']} days"),
        ("Baseline Date", plot["baseline_date"] or "N/A"),
    ]
    _table(pdf, rows)
    pdf.ln(4)

    # ── Scan results ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Scan Results", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)

    alert_str = f"YES - {scan['alert_type']}" if scan["alert_triggered"] else "No"
    ndvi_str = f"{scan['ndvi_mean']:.4f}" if _num_valid(scan["ndvi_mean"]) else "N/A (SAR pass)"
    ndbi_str = f"{scan['ndbi_mean']:.4f}" if _num_valid(scan["ndbi_mean"]) else "N/A (SAR pass)"

    if scan["alert_triggered"] and plot["baseline_ndvi"] and _num_valid(scan["ndvi_mean"]):
        chg = ((scan["ndvi_mean"] - plot["baseline_ndvi"]) / abs(plot["baseline_ndvi"])) * 100
        ndvi_change_str = f"{chg:+.1f}%"
    else:
        ndvi_change_str = "N/A"

    rows = [
        ("Acquisition Date", scan["acquisition_date"] or "N/A"),
        ("Cloud Cover", f"{scan['cloud_cover_pct']:.1f}%" if scan["cloud_cover_pct"] is not None else "N/A"),
        ("NDVI (current)", ndvi_str),
        ("NDVI (baseline)", f"{plot['baseline_ndvi']:.4f}" if plot["baseline_ndvi"] else "N/A"),
        ("NDVI Change", ndvi_change_str),
        ("NDBI (current)", ndbi_str),
        ("Alert Triggered", alert_str),
    ]
    _table(pdf, rows)
    pdf.ln(6)

    # ── Satellite image ───────────────────────────────────────────────────────
    if scan["rgb_image"]:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Satellite Image (True Colour)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        img_bytes = bytes(scan["rgb_image"])
        with io.BytesIO(img_bytes) as buf:
            # fpdf2 can read from BytesIO directly
            x = pdf.get_x()
            y = pdf.get_y()
            try:
                pdf.image(buf, x=x, y=y, w=120)
                pdf.ln(95)  # approximate height
            except Exception:
                pdf.set_font("Helvetica", "I", 9)
                pdf.cell(0, 6, "[Image could not be embedded]",
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Watermark date on image area
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Acquisition date: {scan['acquisition_date']}  |  Source: Copernicus Sentinel-2 / ESA",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # ── Alert section (if applicable) ─────────────────────────────────────────
    if scan["alert_triggered"]:
        pdf.set_fill_color(255, 230, 230)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"ALERT: {(scan['alert_type'] or '').replace('_', ' ').title()}",
                 fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_fill_color(255, 255, 255)
        pdf.multi_cell(0, 6,
                       "An automated analysis detected a significant change in this plot. "
                       "Please verify on-site and contact relevant authorities if encroachment "
                       "or unauthorized construction is confirmed.",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── GeoJSON boundary ──────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Plot Boundary (GeoJSON)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "", 7)
    geojson_pretty = json.dumps(geojson, indent=2)
    # Truncate if very long
    if len(geojson_pretty) > 600:
        geojson_pretty = geojson_pretty[:600] + "\n... (truncated)"
    pdf.set_fill_color(245, 245, 245)
    pdf.multi_cell(0, 4, geojson_pretty, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ── Disclaimer ────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, DISCLAIMER, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table(pdf: FPDF, rows: list[tuple[str, str]]):
    for label, value in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 7, label + ":", border=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, str(value), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _summarise_polygon(geojson: dict) -> str:
    try:
        if geojson["type"] == "Polygon":
            coords = geojson["coordinates"][0]
            centre_lon = sum(c[0] for c in coords) / len(coords)
            centre_lat = sum(c[1] for c in coords) / len(coords)
            return f"~{centre_lat:.4f}°N, {centre_lon:.4f}°E ({len(coords)-1} vertices)"
        if geojson["type"] == "Point":
            return f"{geojson['coordinates'][1]:.4f}°N, {geojson['coordinates'][0]:.4f}°E"
    except Exception:
        pass
    return "See GeoJSON below"


def _num_valid(v) -> bool:
    return v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sqlite3

    # Build mock objects so we can test PDF generation without a real DB
    class FakeRow(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    mock_plot = FakeRow({
        "id": 1,
        "name": "Nanna Farm",
        "geojson_polygon": json.dumps({
            "type": "Polygon",
            "coordinates": [[
                [80.4360, 16.3062], [80.4370, 16.3062],
                [80.4370, 16.3072], [80.4360, 16.3072],
                [80.4360, 16.3062],
            ]],
        }),
        "baseline_ndvi": 0.63,
        "baseline_ndbi": -0.22,
        "baseline_date": "2025-03-01",
        "scan_frequency_days": 5,
    })

    mock_scan = FakeRow({
        "acquisition_date": "2025-03-15",
        "cloud_cover_pct": 12.5,
        "ndvi_mean": 0.41,
        "ndbi_mean": 0.05,
        "alert_triggered": True,
        "alert_type": "encroachment",
        "rgb_image": None,
    })

    pdf_bytes = generate_report(mock_plot, mock_scan)
    out = "test_report.pdf"
    with open(out, "wb") as f:
        f.write(pdf_bytes)
    print(f"Saved {out} ({len(pdf_bytes)} bytes)")
