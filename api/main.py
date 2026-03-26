"""
api/main.py — FastAPI application entry point.

Runs on Railway. Vercel frontend calls this for all data.

Start locally:
    uvicorn api.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import APP_NAME, APP_TAGLINE, WEB_PORT
import db

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on startup and shutdown."""
    log.info(f"[api] Starting {APP_NAME} API...")
    db.init_db()
    yield
    log.info(f"[api] Shutting down {APP_NAME} API...")


app = FastAPI(
    title=f"{APP_NAME} API",
    description=APP_TAGLINE,
    version="2.0.0",
    lifespan=lifespan,
)

# Allow Vercel frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",       # local Next.js dev
        "https://*.vercel.app",        # Vercel preview deployments
        "https://skydeed.in",          # production domain
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
from api.routes import auth, plots, scans, payments, users

app.include_router(auth.router,     prefix="/auth",     tags=["Auth"])
app.include_router(users.router,    prefix="/users",    tags=["Users"])
app.include_router(plots.router,    prefix="/plots",    tags=["Plots"])
app.include_router(scans.router,    prefix="/scans",    tags=["Scans"])
app.include_router(payments.router, prefix="/payments", tags=["Payments"])


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "app":     APP_NAME,
        "tagline": APP_TAGLINE,
        "status":  "running",
        "version": "2.0.0",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
