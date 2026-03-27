"""
api/routes/auth.py — Google OAuth SSO + JWT login.

Flow:
    1. Frontend sends user to GET /auth/google/login
    2. Google redirects back to GET /auth/google/callback
    3. We create/find user in DB
    4. We issue a JWT token
    5. We redirect to frontend dashboard with token in cookie
"""

import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from jose import jwt

from config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI,
    JWT_SECRET, JWT_EXPIRE_DAYS, APP_NAME,
)
import db

log = logging.getLogger(__name__)
router = APIRouter()

# ── Google OAuth setup ─────────────────────────────────────────────────────────
oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/google/login")
async def google_login(request: Request):
    """Step 1 — redirect user to Google consent screen."""
    return await oauth.google.authorize_redirect(request, GOOGLE_REDIRECT_URI)


@router.get("/google/callback")
async def google_callback(request: Request):
    """Step 2 — Google redirects here after user consents."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        log.error(f"[auth] Google OAuth failed: {exc}")
        return RedirectResponse(url="/login?error=oauth_failed")

    user_info = token.get("userinfo", {})
    email     = user_info.get("email", "")
    name      = user_info.get("name", "")
    picture   = user_info.get("picture", "")
    google_sub = user_info.get("sub", "")   # Google's unique user ID

    if not email:
        return RedirectResponse(url="/login?error=no_email")

    # Find or create user in database
    user = db.upsert_web_user(
        email=email,
        name=name,
        picture_url=picture,
        google_sub=google_sub,
    )

    log.info(f"[auth] Login: {email} (user_id={user['id']})")

    # Issue JWT token
    payload = {
        "sub":   str(user["id"]),
        "email": email,
        "name":  name,
        "exp":   datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS),
    }
    access_token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    # Set token in cookie, redirect to frontend dashboard
    frontend_url = os.getenv("FRONTEND_URL", "https://skydeed-frontend.vercel.app")
    response = RedirectResponse(url=f"{frontend_url}/dashboard")
    response.set_cookie(
        key="token",
        value=access_token,
        httponly=True,       # JS cannot read it (security)
        secure=True,         # HTTPS only
        samesite="lax",
        max_age=JWT_EXPIRE_DAYS * 86400,
    )
    return response


@router.post("/logout")
async def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie("token")
    return {"message": "Logged out"}


@router.get("/me")
async def get_me(request: Request):
    """Return current logged-in user info from JWT."""
    from api.deps import get_current_user
    user = get_current_user(request)
    return user
