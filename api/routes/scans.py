"""
api/routes/scans.py — Scan history and results.
"""

import logging
from fastapi import APIRouter, Request, HTTPException

from api.deps import get_current_user
import db

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{plot_id}")
def get_scan_history(plot_id: int, request: Request, limit: int = 20):
    """Return scan history for a plot."""
    user = get_current_user(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    scans = db.get_recent_scans(plot_id, limit=limit)
    return [dict(s) for s in scans]


@router.get("/{plot_id}/latest")
def get_latest_scan(plot_id: int, request: Request):
    """Return the most recent scan for a plot."""
    user = get_current_user(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    scans = db.get_recent_scans(plot_id, limit=1)
    if not scans:
        return {"message": "No scans yet"}

    return dict(scans[0])
