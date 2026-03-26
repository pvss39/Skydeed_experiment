"""
api/routes/users.py — User profile and settings.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.deps import get_current_user
from config import PLANS
import db

router = APIRouter()


class UpdateUserRequest(BaseModel):
    name:     str | None = None
    language: str | None = None
    phone:    str | None = None


@router.get("/me")
def get_profile(request: Request):
    """Return current user profile + subscription info."""
    user = get_current_user(request)
    plan_key = user.get("plan", "starter")
    plan     = PLANS.get(plan_key, PLANS["starter"])

    return {
        "id":                  user["id"],
        "name":                user["name"],
        "email":               user["email"],
        "picture_url":         user.get("picture_url", ""),
        "language":            user.get("language", "en"),
        "subscription_status": user.get("subscription_status", "trial"),
        "plan":                plan_key,
        "plan_name":           plan["name"],
        "max_plots":           plan["plots"],
        "scan_frequency_days": plan["scan_days"],
    }


@router.patch("/me")
def update_profile(body: UpdateUserRequest, request: Request):
    """Update user profile settings."""
    user = get_current_user(request)

    with db.get_conn() as conn:
        if body.name:
            conn.execute("UPDATE users SET name=? WHERE id=?", (body.name, user["id"]))
        if body.language:
            conn.execute("UPDATE users SET language=? WHERE id=?", (body.language, user["id"]))
        if body.phone:
            conn.execute("UPDATE users SET phone=? WHERE id=?", (body.phone, user["id"]))

    return {"message": "Profile updated"}
