"""
api/deps.py — Shared dependencies for FastAPI routes.

get_current_user() — reads JWT from cookie, returns user dict.
Use as a dependency in any route that needs login.
"""

from fastapi import Request, HTTPException, status
from jose import jwt, JWTError

from config import JWT_SECRET
import db


def get_current_user(request: Request) -> dict:
    """
    Read JWT token from Authorization header (Bearer) or cookie.
    Returns user dict from DB.
    Raises 401 if not logged in or token invalid.
    """
    # Try Authorization: Bearer <token> header first (frontend uses this)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get("token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in",
        )

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return dict(user)


def require_subscription(request: Request) -> dict:
    """
    Like get_current_user but also checks subscription is active.
    Raises 403 if not subscribed.
    """
    user = get_current_user(request)
    if user.get("subscription_status") not in ("active", "trial"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription required. Please upgrade your plan.",
        )
    return user
