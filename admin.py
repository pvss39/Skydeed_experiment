"""
admin.py — Operator CLI for LandSentinel.

You (the service operator) use this to manage customers and plots
without needing Telegram. Customers never touch this script.

Usage examples:
  python admin.py add-customer --name "Ravi Kumar" --chat-id 123456789
  python admin.py add-plot --chat-id 123456789 --name "North Field" --coords "16.3067,80.4365"
  python admin.py add-plot --chat-id 123456789 --name "South Field" --geojson plot.geojson --freq 3
  python admin.py list-customers
  python admin.py list-plots --chat-id 123456789
  python admin.py scan --plot-id 4
  python admin.py scan-all
  python admin.py deactivate-plot --plot-id 4
  python admin.py export-plots --out plots_export.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

import db
from satellite import point_to_polygon, coords_to_polygon


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_add_customer(args):
    db.init_db()
    user_id = db.upsert_user(
        chat_id=args.chat_id,
        name=args.name,
        language=args.language,
    )
    print(f"[OK] Customer '{args.name}' saved. User ID: {user_id}  Chat ID: {args.chat_id}")


def cmd_add_plot(args):
    db.init_db()

    # Resolve customer
    user = db.get_user_by_chat_id(args.chat_id)
    if not user:
        # Auto-create a minimal user record if customer not yet in DB
        db.upsert_user(args.chat_id, name=args.chat_id)
        user = db.get_user_by_chat_id(args.chat_id)
        log.warning(f"Customer {args.chat_id} not found — created minimal record. "
                    f"Run add-customer to set their name.")

    # Build polygon
    if args.geojson:
        geo_path = Path(args.geojson)
        if not geo_path.exists():
            print(f"[ERROR] GeoJSON file not found: {args.geojson}")
            sys.exit(1)
        geojson = json.loads(geo_path.read_text())
        # Accept FeatureCollection (take first feature), Feature, or raw Polygon/MultiPolygon
        if geojson.get("type") == "FeatureCollection":
            geojson = geojson["features"][0]["geometry"]
        elif geojson.get("type") == "Feature":
            geojson = geojson["geometry"]

    elif args.coords:
        pairs = _parse_coords(args.coords)
        if len(pairs) == 1:
            geojson = point_to_polygon(pairs[0][0], pairs[0][1],
                                       size_deg=args.size_deg)
        else:
            geojson = coords_to_polygon(pairs)
    else:
        print("[ERROR] Provide --coords or --geojson")
        sys.exit(1)

    plot_id = db.create_plot(
        user_id=user["id"],
        name=args.name,
        geojson_polygon=geojson,
        scan_frequency_days=args.freq,
    )
    print(f"[OK] Plot '{args.name}' created. Plot ID: {plot_id}")
    print(f"     Customer: {user['name']} (chat_id={args.chat_id})")
    print(f"     Scan frequency: every {args.freq} days")
    print(f"     Run baseline scan: python admin.py scan --plot-id {plot_id}")


def cmd_list_customers(args):
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.*, COUNT(p.id) as plot_count "
            "FROM users u LEFT JOIN plots p ON p.user_id = u.id "
            "GROUP BY u.id ORDER BY u.created_at"
        ).fetchall()

    if not rows:
        print("No customers registered.")
        return

    print(f"\n{'ID':<5} {'Name':<25} {'Chat ID':<15} {'Lang':<5} {'Plots':<6} {'Since'}")
    print("-" * 70)
    for r in rows:
        print(f"{r['id']:<5} {(r['name'] or '-'):<25} {r['telegram_chat_id']:<15} "
              f"{r['language']:<5} {r['plot_count']:<6} {r['created_at'][:10]}")
    print()


def cmd_list_plots(args):
    db.init_db()

    if args.chat_id:
        user = db.get_user_by_chat_id(args.chat_id)
        if not user:
            print(f"No customer with chat_id {args.chat_id}")
            return
        plots = db.get_user_plots(user["id"])
        print(f"\nPlots for {user['name']} (chat_id={args.chat_id}):")
    else:
        with db.get_conn() as conn:
            plots = conn.execute(
                "SELECT p.*, u.name as customer_name, u.telegram_chat_id "
                "FROM plots p JOIN users u ON u.id = p.user_id "
                "WHERE p.is_active = 1 ORDER BY p.created_at"
            ).fetchall()
        print("\nAll active plots:")

    if not plots:
        print("  None.")
        return

    print(f"\n{'ID':<5} {'Name':<20} {'Customer':<20} {'Freq':<6} {'Last Scan':<12} {'Baseline':<10} {'NDVI'}")
    print("-" * 85)
    for p in plots:
        customer = p['customer_name'] if 'customer_name' in p.keys() else ''
        baseline = p['baseline_date'] or 'None'
        last = p['last_scan_date'] or 'Never'
        ndvi = f"{p['baseline_ndvi']:.3f}" if p['baseline_ndvi'] else 'N/A'
        print(f"{p['id']:<5} {p['name']:<20} {customer:<20} {p['scan_frequency_days']:<6}d "
              f"{last:<12} {baseline:<10} {ndvi}")
    print()


def cmd_scan(args):
    db.init_db()
    from pipeline import run_scan_for_plot, _ensure_gee
    _ensure_gee()

    plot = db.get_plot(args.plot_id)
    if not plot:
        print(f"[ERROR] Plot ID {args.plot_id} not found")
        sys.exit(1)

    print(f"[scan] Running scan for plot '{plot['name']}' (id={args.plot_id}) ...")
    asyncio.run(run_scan_for_plot(args.plot_id, bot=None))
    print("[scan] Done. Check landsentinel.db for results.")


def cmd_scan_all(args):
    db.init_db()
    from pipeline import run_scan_for_plot, _ensure_gee
    _ensure_gee()

    due = db.get_plots_due_for_scan()
    if not due:
        print("[scan-all] No plots due for scan today.")
        return

    print(f"[scan-all] {len(due)} plot(s) due:")
    for p in due:
        print(f"  Scanning '{p['name']}' (id={p['id']}) ...")
        try:
            asyncio.run(run_scan_for_plot(p["id"], bot=None))
            print(f"  Done.")
        except Exception as exc:
            print(f"  FAILED: {exc}")


def cmd_deactivate(args):
    db.init_db()
    with db.get_conn() as conn:
        plot = conn.execute("SELECT * FROM plots WHERE id=?", (args.plot_id,)).fetchone()
        if not plot:
            print(f"[ERROR] Plot {args.plot_id} not found")
            sys.exit(1)
        conn.execute("UPDATE plots SET is_active=0 WHERE id=?", (args.plot_id,))
    print(f"[OK] Plot '{plot['name']}' (id={args.plot_id}) deactivated.")


def cmd_export(args):
    db.init_db()
    with db.get_conn() as conn:
        plots = conn.execute(
            "SELECT p.*, u.name as customer_name, u.telegram_chat_id "
            "FROM plots p JOIN users u ON u.id = p.user_id "
            "WHERE p.is_active = 1 ORDER BY p.created_at"
        ).fetchall()

    export = []
    for p in plots:
        export.append({
            "plot_id": p["id"],
            "plot_name": p["name"],
            "customer_name": p["customer_name"],
            "telegram_chat_id": p["telegram_chat_id"],
            "geojson_polygon": json.loads(p["geojson_polygon"]),
            "baseline_ndvi": p["baseline_ndvi"],
            "baseline_date": p["baseline_date"],
            "last_scan_date": p["last_scan_date"],
            "scan_frequency_days": p["scan_frequency_days"],
        })

    out = Path(args.out)
    out.write_text(json.dumps(export, indent=2))
    print(f"[OK] Exported {len(export)} plots to {out}")


def cmd_set_freq(args):
    db.init_db()
    with db.get_conn() as conn:
        plot = conn.execute("SELECT * FROM plots WHERE id=?", (args.plot_id,)).fetchone()
        if not plot:
            print(f"[ERROR] Plot {args.plot_id} not found")
            sys.exit(1)
        conn.execute("UPDATE plots SET scan_frequency_days=? WHERE id=?",
                     (args.freq, args.plot_id))
    print(f"[OK] Plot '{plot['name']}' scan frequency updated to every {args.freq} days.")


def cmd_scan_history(args):
    db.init_db()
    plot = db.get_plot(args.plot_id)
    if not plot:
        print(f"[ERROR] Plot {args.plot_id} not found")
        sys.exit(1)

    scans = db.get_recent_scans(args.plot_id, limit=args.limit)
    print(f"\nScan history for '{plot['name']}' (last {args.limit}):")
    print(f"{'ID':<5} {'Date':<12} {'NDVI':<8} {'NDBI':<8} {'Cloud%':<8} {'Alert':<12} {'Type'}")
    print("-" * 70)
    for s in scans:
        alert = "YES" if s["alert_triggered"] else "No"
        atype = s["alert_type"] or "-"
        ndvi = f"{s['ndvi_mean']:.3f}" if s["ndvi_mean"] is not None else "N/A"
        ndbi = f"{s['ndbi_mean']:.3f}" if s["ndbi_mean"] is not None else "N/A"
        cloud = f"{s['cloud_cover_pct']:.1f}" if s["cloud_cover_pct"] is not None else "N/A"
        print(f"{s['id']:<5} {(s['acquisition_date'] or '-'):<12} {ndvi:<8} {ndbi:<8} "
              f"{cloud:<8} {alert:<12} {atype}")
    print()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_coords(coords_str: str) -> list[tuple[float, float]]:
    """
    Parse coordinate string. Supports:
      - Single point: "16.3067,80.4365"
      - Multiple points: "16.3067,80.4365;16.3072,80.4370;..."
      - Space-separated: "16.3067,80.4365 16.3072,80.4370"
    """
    pairs = []
    # Split on semicolons or spaces
    tokens = coords_str.replace(";", " ").split()
    for token in tokens:
        parts = token.strip().split(",")
        if len(parts) != 2:
            print(f"[ERROR] Bad coordinate: '{token}'. Expected lat,lon")
            sys.exit(1)
        pairs.append((float(parts[0]), float(parts[1])))
    return pairs


# ── CLI parser ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="admin.py",
        description="LandSentinel operator CLI — manage customers and plots",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add-customer
    p = sub.add_parser("add-customer", help="Register a new customer")
    p.add_argument("--chat-id", required=True, help="Telegram chat ID")
    p.add_argument("--name", required=True, help="Customer full name")
    p.add_argument("--language", default="en", choices=["en", "te", "hi"],
                   help="Preferred language (default: en)")

    # add-plot
    p = sub.add_parser("add-plot", help="Add a land plot for a customer")
    p.add_argument("--chat-id", required=True, help="Customer Telegram chat ID")
    p.add_argument("--name", required=True, help="Plot name")
    p.add_argument("--coords",
                   help='Centre point or polygon: "lat,lon" or "lat1,lon1;lat2,lon2;..."')
    p.add_argument("--geojson",
                   help="Path to GeoJSON file (Polygon, Feature, or FeatureCollection)")
    p.add_argument("--freq", type=int, default=5,
                   help="Scan frequency in days (default: 5)")
    p.add_argument("--size-deg", type=float, default=0.005,
                   help="Square plot half-size in degrees when using single point (default: 0.005 ~500m)")

    # list-customers
    sub.add_parser("list-customers", help="Show all customers")

    # list-plots
    p = sub.add_parser("list-plots", help="Show plots (all, or filtered by customer)")
    p.add_argument("--chat-id", help="Filter by customer Telegram chat ID")

    # scan
    p = sub.add_parser("scan", help="Run immediate scan for one plot (no Telegram delivery)")
    p.add_argument("--plot-id", type=int, required=True)

    # scan-all
    sub.add_parser("scan-all", help="Run scans for all plots due today (no Telegram delivery)")

    # deactivate-plot
    p = sub.add_parser("deactivate-plot", help="Deactivate a plot (stop scanning)")
    p.add_argument("--plot-id", type=int, required=True)

    # set-freq
    p = sub.add_parser("set-freq", help="Change scan frequency for a plot")
    p.add_argument("--plot-id", type=int, required=True)
    p.add_argument("--freq", type=int, required=True, help="Days between scans")

    # scan-history
    p = sub.add_parser("scan-history", help="Show scan history for a plot")
    p.add_argument("--plot-id", type=int, required=True)
    p.add_argument("--limit", type=int, default=10)

    # export-plots
    p = sub.add_parser("export-plots", help="Export all active plots to JSON")
    p.add_argument("--out", default="plots_export.json")

    return parser


COMMANDS = {
    "add-customer":   cmd_add_customer,
    "add-plot":       cmd_add_plot,
    "list-customers": cmd_list_customers,
    "list-plots":     cmd_list_plots,
    "scan":           cmd_scan,
    "scan-all":       cmd_scan_all,
    "deactivate-plot": cmd_deactivate,
    "set-freq":       cmd_set_freq,
    "scan-history":   cmd_scan_history,
    "export-plots":   cmd_export,
}

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    COMMANDS[args.command](args)
