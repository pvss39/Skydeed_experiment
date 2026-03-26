"""
api/routes/plots.py — CRUD for farm plots.

All routes require login.
"""

import json
import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from api.deps import get_current_user, require_subscription
from config import PLANS
import db

log = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ─────────────────────────────────────────────────────────────

class CreatePlotRequest(BaseModel):
    name:                str
    geojson_polygon:     dict
    scan_frequency_days: int = 5


class UpdatePlotRequest(BaseModel):
    name:                str | None = None
    scan_frequency_days: int | None = None


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/")
def list_plots(request: Request):
    """Return all plots for the logged-in user."""
    user = get_current_user(request)
    plots = db.get_user_plots(user["id"])
    return [dict(p) for p in plots]


@router.post("/")
def create_plot(body: CreatePlotRequest, request: Request):
    """Register a new farm plot."""
    user = require_subscription(request)

    # Check plot limit based on subscription plan
    plan_key  = user.get("plan", "starter")
    plan      = PLANS.get(plan_key, PLANS["starter"])
    max_plots = plan["plots"]

    existing = db.get_user_plots(user["id"])
    if len(existing) >= max_plots:
        raise HTTPException(
            status_code=403,
            detail=f"Your {plan['name']} plan allows {max_plots} plot(s). Upgrade to add more.",
        )

    plot_id = db.create_plot(
        user_id=user["id"],
        name=body.name,
        geojson_polygon=body.geojson_polygon,
        scan_frequency_days=body.scan_frequency_days,
    )

    log.info(f"[plots] Created plot '{body.name}' (id={plot_id}) for user {user['id']}")

    # Queue first baseline scan
    from workers.tasks import scan_plot_task
    scan_plot_task.delay(plot_id)

    return {"plot_id": plot_id, "message": "Plot registered. Baseline scan queued."}


@router.get("/{plot_id}")
def get_plot(plot_id: int, request: Request):
    """Get one plot by ID."""
    user = get_current_user(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    return dict(plot)


@router.patch("/{plot_id}")
def update_plot(plot_id: int, body: UpdatePlotRequest, request: Request):
    """Update plot name or scan frequency."""
    user = get_current_user(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    with db.get_conn() as conn:
        if body.name is not None:
            conn.execute("UPDATE plots SET name=? WHERE id=?", (body.name, plot_id))
        if body.scan_frequency_days is not None:
            conn.execute("UPDATE plots SET scan_frequency_days=? WHERE id=?",
                         (body.scan_frequency_days, plot_id))

    return {"message": "Plot updated"}


@router.delete("/{plot_id}")
def delete_plot(plot_id: int, request: Request):
    """Deactivate a plot (stop scanning)."""
    user = get_current_user(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    with db.get_conn() as conn:
        conn.execute("UPDATE plots SET is_active=0 WHERE id=?", (plot_id,))

    return {"message": "Plot deactivated"}


@router.post("/{plot_id}/scan")
def trigger_scan(plot_id: int, request: Request):
    """Manually trigger an immediate scan for a plot."""
    user = require_subscription(request)
    plot = db.get_plot(plot_id)

    if not plot or plot["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plot not found")

    from workers.tasks import scan_plot_task
    scan_plot_task.delay(plot_id)

    return {"message": f"Scan queued for '{plot['name']}'"}
