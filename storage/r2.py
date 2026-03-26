"""
storage/r2.py — Cloudflare R2 image storage.

R2 is an S3-compatible object store (Cloudflare's version).
We use boto3 with a custom endpoint URL pointing to R2.

Why R2 instead of storing BLOBs in DB?
- DB stays small (fast queries, cheap Railway plan)
- Images served directly from R2 CDN (fast downloads for users)
- R2 free tier: 10 GB storage, 1M reads/month — plenty for 5000 users

Usage:
    url = upload_image(image_bytes, "plots/42/scan_20240315.png")
    # Returns: "https://pub-xxxx.r2.dev/plots/42/scan_20240315.png"
"""

import logging
import boto3
from botocore.client import Config

from config import (
    R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY,
    R2_ENDPOINT_URL,
    R2_BUCKET_NAME,
    R2_PUBLIC_URL,
)

log = logging.getLogger(__name__)

# Lazy singleton — created on first use
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",  # R2 requires "auto"
        )
    return _client


def upload_image(
    image_bytes: bytes,
    key: str,
    content_type: str = "image/png",
) -> str:
    """
    Upload image bytes to R2 and return its public URL.

    Args:
        image_bytes: Raw PNG/JPEG bytes
        key: Object key, e.g. "plots/42/scan_20240315_rgb.png"
        content_type: MIME type (default: image/png)

    Returns:
        Public URL string, e.g. "https://pub-xxxx.r2.dev/plots/42/scan_20240315_rgb.png"
    """
    client = _get_client()
    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=image_bytes,
        ContentType=content_type,
    )
    url = f"{R2_PUBLIC_URL.rstrip('/')}/{key}"
    log.info(f"[r2] Uploaded {key} ({len(image_bytes)//1024} KB) → {url}")
    return url


def delete_image(key: str) -> None:
    """Delete an object from R2 (e.g. when a plot is deleted)."""
    client = _get_client()
    client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
    log.info(f"[r2] Deleted {key}")


def scan_image_key(plot_id: int, scan_date: str, kind: str = "rgb") -> str:
    """
    Generate a consistent R2 key for a scan image.

    Args:
        plot_id: DB plot ID
        scan_date: ISO date string, e.g. "2024-03-15"
        kind: "rgb" | "ndvi"

    Returns:
        Key string: "plots/42/2024-03-15_rgb.png"
    """
    return f"plots/{plot_id}/{scan_date}_{kind}.png"


def baseline_image_key(plot_id: int, kind: str = "rgb") -> str:
    """Key for a plot's baseline image: "plots/42/baseline_rgb.png"."""
    return f"plots/{plot_id}/baseline_{kind}.png"
