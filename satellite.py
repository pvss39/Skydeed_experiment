"""
satellite.py — Fetch REAL satellite imagery via Google Earth Engine.

Source priority per plot scan:
  1. Sentinel-2 SR Harmonized (10m, 5-day revisit, cloud < 30%)
  2. Landsat 8/9 Collection 2 L2 (30m, 16-day revisit, cloud < 30%)
  3. Sentinel-1 SAR (all-weather fallback, no optical/NDVI)

Returns actual PNG bytes downloaded from GEE — no synthetic images.
"""

import os
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import ee
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

ENABLE_MAPBOX = os.getenv("ENABLE_MAPBOX", "false").strip().lower() == "true"
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "")

# ── GEE initialisation ────────────────────────────────────────────────────────

def init_gee():
    """Initialise GEE. Tries service account key first, falls back to default creds."""
    project = os.getenv("GEE_PROJECT")
    key_file = os.getenv("GEE_PRIVATE_KEY_FILE")
    sa_email = os.getenv("GEE_SERVICE_ACCOUNT_EMAIL")

    if key_file and key_file.strip() and Path(key_file).exists() and sa_email and sa_email.strip():
        credentials = ee.ServiceAccountCredentials(sa_email, key_file)
        ee.Initialize(credentials=credentials, project=project)
        log.info("[gee] Initialized with service account")
    else:
        ee.Initialize(project=project)
        log.info("[gee] Initialized with default credentials (earthengine authenticate)")


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_plot_imagery(geojson_polygon: dict, days_back: int = 15,
                       plot_name: str = "") -> dict | None:
    """
    Fetch real imagery for a GeoJSON polygon.

    Source priority:
      1. Sentinel-2 SR Harmonized  (10m, best resolution)
      2. Landsat 8/9 Collection 2  (30m, wider coverage + longer history)
      3. Sentinel-1 SAR            (all-weather, no NDVI)

    Returns dict with keys:
        rgb_png, ndvi_png, ndvi_mean, ndbi_mean,
        cloud_cover_pct, acquisition_date, source

    Returns None if absolutely no imagery is found.
    """
    try:
        geometry = _geojson_to_ee(geojson_polygon)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days_back)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        result = _fetch_sentinel2(geometry, start_str, end_str)
        if result is None:
            log.info("[sat] Sentinel-2 unavailable — trying Landsat 8/9")
            result = _fetch_landsat(geometry, start_str, end_str)
        if result is None:
            log.info("[sat] Landsat unavailable — falling back to Sentinel-1 SAR")
            result = _fetch_sentinel1_sar(geometry, start_str, end_str)
        if result is None:
            return None

        # Draw plot boundary overlay on the GEE RGB image
        try:
            bounds = _polygon_bounds(geojson_polygon)
            source_label = result["source"] + " | 10m"
            result["rgb_png"] = draw_plot_boundary(
                result["rgb_png"], geojson_polygon, bounds,
                plot_name=plot_name,
                acq_date=result["acquisition_date"],
                source=source_label,
            )
        except Exception as exc:
            log.warning(f"[sat] draw_plot_boundary failed (non-fatal): {exc}")

        # Mapbox visual layer (only when ENABLE_MAPBOX=true)
        mapbox_png = None
        if ENABLE_MAPBOX and MAPBOX_TOKEN:
            lat, lon = _geojson_centroid(geojson_polygon)
            mapbox_png = _fetch_mapbox_image(lat, lon)
        result["mapbox_png"] = mapbox_png
        return result

    except Exception as exc:
        log.error(f"[sat] fetch_plot_imagery failed: {exc}", exc_info=True)
        return None


# ── Sentinel-2 optical ────────────────────────────────────────────────────────

def _fetch_sentinel2(geometry: ee.Geometry, start: str, end: str) -> dict | None:
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geometry)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )

    count = collection.size().getInfo()
    if count == 0:
        log.info("[sat] Sentinel-2: no images with cloud < 30% in range")
        return None

    image = collection.first()
    props = image.toDictionary(["system:time_start", "CLOUDY_PIXEL_PERCENTAGE"]).getInfo()
    acq_ts = props.get("system:time_start", 0)
    acq_date = datetime.utcfromtimestamp(acq_ts / 1000).strftime("%Y-%m-%d")
    cloud_pct = float(props.get("CLOUDY_PIXEL_PERCENTAGE", 0))

    # Spectral indices
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndbi = image.normalizedDifference(["B11", "B8"]).rename("NDBI")

    # Compute mean values over the plot polygon
    ndvi_mean = _band_mean(ndvi, geometry, "NDVI")
    ndbi_mean = _band_mean(ndbi, geometry, "NDBI")

    # Download PNGs from GEE
    rgb_png = _download_thumb(
        image.select(["B4", "B3", "B2"]),
        geometry,
        vis_params={"min": 0, "max": 3000, "gamma": 1.4},
    )
    ndvi_png = _download_thumb(
        ndvi,
        geometry,
        vis_params={"min": -0.2, "max": 0.8, "palette": ["red", "yellow", "green"]},
    )

    if rgb_png is None or ndvi_png is None:
        log.error("[sat] Failed to download thumb images from GEE")
        return None

    return {
        "rgb_png": rgb_png,
        "ndvi_png": ndvi_png,
        "ndvi_mean": ndvi_mean,
        "ndbi_mean": ndbi_mean,
        "cloud_cover_pct": cloud_pct,
        "acquisition_date": acq_date,
        "source": "Sentinel-2",
    }


# ── Landsat 8/9 Collection 2 L2 ──────────────────────────────────────────────

# Landsat band mapping (Collection 2 L2 SR)
# B2=Blue, B3=Green, B4=Red, B5=NIR, B6=SWIR1, B7=SWIR2
_LANDSAT_COLLECTIONS = [
    "LANDSAT/LC09/C02/T1_L2",  # Landsat 9 (2021-present, newer sensor)
    "LANDSAT/LC08/C02/T1_L2",  # Landsat 8 (2013-present)
]
_LANDSAT_SCALE_FACTOR = 0.0000275
_LANDSAT_OFFSET = -0.2


def _fetch_landsat(geometry: ee.Geometry, start: str, end: str) -> dict | None:
    """
    Fetch Landsat 8 or 9 SR imagery. Tries L9 first (newer), falls back to L8.
    Cloud mask applied via QA_PIXEL band. Returns 30m imagery.
    """
    for collection_id in _LANDSAT_COLLECTIONS:
        result = _fetch_one_landsat(collection_id, geometry, start, end)
        if result is not None:
            return result
    return None


def _fetch_one_landsat(collection_id: str, geometry: ee.Geometry,
                       start: str, end: str) -> dict | None:
    sat_name = "Landsat-9" if "LC09" in collection_id else "Landsat-8"

    collection = (
        ee.ImageCollection(collection_id)
        .filterBounds(geometry)
        .filterDate(start, end)
        .map(_landsat_cloud_mask)
        .filter(ee.Filter.lt("CLOUD_COVER", 30))
        .sort("CLOUD_COVER")
    )

    count = collection.size().getInfo()
    if count == 0:
        log.info(f"[sat] {sat_name}: no cloud-free images in range")
        return None

    image = collection.first()
    props = image.toDictionary(["system:time_start", "CLOUD_COVER"]).getInfo()
    acq_ts = props.get("system:time_start", 0)
    acq_date = datetime.utcfromtimestamp(acq_ts / 1000).strftime("%Y-%m-%d")
    cloud_pct = float(props.get("CLOUD_COVER", 0))

    # Apply scale factors to get reflectance values
    optical = image.select(["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6"]) \
                   .multiply(_LANDSAT_SCALE_FACTOR).add(_LANDSAT_OFFSET)

    blue  = optical.select("SR_B2")
    green = optical.select("SR_B3")
    red   = optical.select("SR_B4")
    nir   = optical.select("SR_B5")
    swir1 = optical.select("SR_B6")

    # Spectral indices (same formulas, different band names)
    ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")
    ndbi = swir1.subtract(nir).divide(swir1.add(nir)).rename("NDBI")

    ndvi_mean = _band_mean(ndvi, geometry, "NDVI", scale=30)
    ndbi_mean = _band_mean(ndbi, geometry, "NDBI", scale=30)

    rgb_image = ee.Image.cat([red, green, blue])
    rgb_png = _download_thumb(
        rgb_image,
        geometry,
        vis_params={"min": 0.0, "max": 0.3, "gamma": 1.4},
    )
    ndvi_png = _download_thumb(
        ndvi,
        geometry,
        vis_params={"min": -0.2, "max": 0.8, "palette": ["red", "yellow", "green"]},
    )

    if rgb_png is None or ndvi_png is None:
        log.error(f"[sat] {sat_name}: failed to download thumb images")
        return None

    log.info(f"[sat] Got {sat_name} image: {acq_date}, cloud={cloud_pct:.1f}%")
    return {
        "rgb_png": rgb_png,
        "ndvi_png": ndvi_png,
        "ndvi_mean": ndvi_mean,
        "ndbi_mean": ndbi_mean,
        "cloud_cover_pct": cloud_pct,
        "acquisition_date": acq_date,
        "source": sat_name,
    }


def _landsat_cloud_mask(image: ee.Image) -> ee.Image:
    """Mask clouds and cloud shadows using Landsat QA_PIXEL band."""
    qa = image.select("QA_PIXEL")
    cloud        = qa.bitwiseAnd(1 << 3).eq(0)   # bit 3: cloud
    cloud_shadow = qa.bitwiseAnd(1 << 4).eq(0)   # bit 4: cloud shadow
    mask = cloud.And(cloud_shadow)
    return image.updateMask(mask)


# ── Sentinel-1 SAR fallback ───────────────────────────────────────────────────

def _fetch_sentinel1_sar(geometry: ee.Geometry, start: str, end: str) -> dict | None:
    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(geometry)
        .filterDate(start, end)
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .sort("system:time_start", False)
    )

    count = collection.size().getInfo()
    if count == 0:
        log.info("[sat] Sentinel-1 SAR: no images in range")
        return None

    image = collection.first()
    props = image.toDictionary(["system:time_start"]).getInfo()
    acq_ts = props.get("system:time_start", 0)
    acq_date = datetime.utcfromtimestamp(acq_ts / 1000).strftime("%Y-%m-%d")

    vv = image.select("VV")
    vh = image.select("VH") if "VH" in image.bandNames().getInfo() else vv

    # SAR false-colour: VV, VH, VV/VH ratio
    ratio = vv.divide(vh).rename("ratio")
    sar_rgb = ee.Image.cat([vv, vh, ratio])

    rgb_png = _download_thumb(
        sar_rgb,
        geometry,
        vis_params={"min": [-25, -30, 0], "max": [0, -5, 2]},
    )

    if rgb_png is None:
        return None

    # No NDVI from SAR — return NaN proxies so pipeline can handle gracefully
    # mapbox_png added by fetch_plot_imagery() caller
    return {
        "rgb_png": rgb_png,
        "ndvi_png": rgb_png,  # SAR doesn't produce NDVI; reuse SAR image
        "mapbox_png": None,   # set by fetch_plot_imagery if Mapbox enabled
        "ndvi_mean": float("nan"),
        "ndbi_mean": float("nan"),
        "cloud_cover_pct": 0.0,
        "acquisition_date": acq_date,
        "source": "Sentinel-1-SAR",
    }


# ── GEE helpers ───────────────────────────────────────────────────────────────

def _geojson_to_ee(geojson: dict) -> ee.Geometry:
    """Convert a GeoJSON dict (Polygon or Feature) to ee.Geometry."""
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    if geojson.get("type") == "Polygon":
        return ee.Geometry.Polygon(geojson["coordinates"])
    if geojson.get("type") == "Point":
        lon, lat = geojson["coordinates"]
        # Create a small 500m buffer around the point
        return ee.Geometry.Point([lon, lat]).buffer(500)
    raise ValueError(f"Unsupported GeoJSON type: {geojson.get('type')}")


def _band_mean(image: ee.Image, geometry: ee.Geometry, band: str,
               scale: int = 10) -> float:
    try:
        result = image.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geometry,
            scale=scale,
            maxPixels=1e9,
        ).getInfo()
        val = result.get(band)
        return float(val) if val is not None else float("nan")
    except Exception as exc:
        log.warning(f"[sat] band_mean({band}) failed: {exc}")
        return float("nan")


def _download_thumb(image: ee.Image, geometry: ee.Geometry,
                    vis_params: dict, dimensions: int = 512) -> bytes | None:
    """Download a real PNG thumbnail from GEE getThumbURL."""
    try:
        url = image.getThumbURL({
            "region": geometry,
            "dimensions": dimensions,
            "format": "PNG",
            **vis_params,
        })
        log.info(f"[sat] Downloading from GEE: {url[:80]}...")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        if len(resp.content) < 1000:
            log.warning("[sat] Downloaded image suspiciously small — may be error tile")
            return None
        return resp.content
    except Exception as exc:
        log.error(f"[sat] _download_thumb failed: {exc}")
        return None


# ── Polygon builder helpers ───────────────────────────────────────────────────

def point_to_polygon(lat: float, lon: float, size_deg: float = 0.005) -> dict:
    """Create a square GeoJSON Polygon around a centre point (~500m at equator)."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - size_deg, lat - size_deg],
            [lon + size_deg, lat - size_deg],
            [lon + size_deg, lat + size_deg],
            [lon - size_deg, lat + size_deg],
            [lon - size_deg, lat - size_deg],
        ]],
    }


def coords_to_polygon(coord_pairs: list[tuple[float, float]]) -> dict:
    """Convert [(lat, lon), ...] pairs into a GeoJSON Polygon."""
    # GeoJSON uses [lon, lat]
    coords = [[lon, lat] for lat, lon in coord_pairs]
    if coords[0] != coords[-1]:
        coords.append(coords[0])  # close the ring
    return {"type": "Polygon", "coordinates": [coords]}


# ── Mapbox static image ───────────────────────────────────────────────────────

def _geojson_centroid(geojson: dict) -> tuple[float, float]:
    """Return (lat, lon) centroid of a GeoJSON Polygon."""
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    coords = geojson["coordinates"][0]  # outer ring, list of [lon, lat]
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lat, lon


def _fetch_mapbox_image(lat: float, lon: float, zoom: int = 17,
                        size: str = "1280x1280") -> bytes | None:
    """Fetch a Mapbox satellite-streets static image. Returns PNG bytes or None."""
    if not MAPBOX_TOKEN:
        log.warning("[sat] Mapbox enabled but MAPBOX_TOKEN not set")
        return None
    url = (
        f"https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/static"
        f"/{lon},{lat},{zoom},0/{size}@2x"
        f"?access_token={MAPBOX_TOKEN}"
    )
    try:
        log.info(f"[sat] Fetching Mapbox image for {lat:.4f},{lon:.4f}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        if len(resp.content) < 1000:
            log.warning("[sat] Mapbox image suspiciously small")
            return None
        return resp.content
    except Exception as exc:
        log.error(f"[sat] Mapbox fetch failed: {exc}")
        return None


# ── Plot boundary overlay ─────────────────────────────────────────────────────

def _polygon_bounds(geojson: dict) -> dict:
    """Compute bounding box of a GeoJSON polygon. Returns {west, east, south, north}."""
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    coords = geojson["coordinates"][0]  # outer ring [[lon, lat], ...]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return {"west": min(lons), "east": max(lons), "south": min(lats), "north": max(lats)}


def draw_plot_boundary(
    image_bytes: bytes,
    geojson_polygon: dict,
    image_bounds: dict,
    plot_name: str = "",
    acq_date: str = "",
    source: str = "Sentinel-2 | 10m",
) -> bytes:
    """
    Draw the GeoJSON polygon boundary on a PNG image using Pillow.

    - Red outline, 3px stroke
    - Semi-transparent red fill (alpha=40)
    - White label top-left: plot_name, acq_date, source

    Args:
        image_bytes: Raw PNG bytes
        geojson_polygon: GeoJSON Polygon or Feature
        image_bounds: {"west": lon, "east": lon, "south": lat, "north": lat}
        plot_name: Plot label line 1
        acq_date: Acquisition date label line 2
        source: Source label line 3
    """
    from PIL import Image, ImageDraw, ImageFont

    geojson = geojson_polygon
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    coords = geojson["coordinates"][0]  # outer ring [[lon, lat], ...]

    west  = image_bounds["west"]
    east  = image_bounds["east"]
    south = image_bounds["south"]
    north = image_bounds["north"]

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    def geo_to_px(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - west) / (east - west) * width
        y = (north - lat) / (north - south) * height  # y=0 is top (north)
        return (x, y)

    pixel_coords = [geo_to_px(c[0], c[1]) for c in coords]

    # Semi-transparent red fill on a separate RGBA overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    draw_ov.polygon(pixel_coords, fill=(255, 0, 0, 40))
    img = Image.alpha_composite(img, overlay)

    # Red outline (3px) drawn on the composited image
    draw = ImageDraw.Draw(img)
    # draw.line closes the polygon by repeating the start coord if needed
    ring = pixel_coords if pixel_coords[0] == pixel_coords[-1] else pixel_coords + [pixel_coords[0]]
    draw.line(ring, fill=(255, 0, 0, 255), width=3)

    # White label — top-left corner
    label_lines = [l for l in [plot_name, acq_date, source] if l]
    if label_lines:
        font = None
        for font_name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(font_name, 15)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()

        x, y = 8, 8
        for line in label_lines:
            # Drop-shadow for readability on any background
            draw.text((x + 1, y + 1), line, fill=(0, 0, 0, 200), font=font)
            draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
            bbox = draw.textbbox((x, y), line, font=font)
            y += (bbox[3] - bbox[1]) + 3

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    init_gee()

    lat = float(os.getenv("TEST_PLOT_LAT", "16.3067"))
    lon = float(os.getenv("TEST_PLOT_LON", "80.4365"))
    polygon = point_to_polygon(lat, lon)

    print(f"Fetching imagery for {lat}, {lon} ...")
    result = fetch_plot_imagery(polygon, days_back=30)

    if result is None:
        print("No imagery found.")
    else:
        print(f"Source: {result['source']}")
        print(f"Date: {result['acquisition_date']}")
        print(f"NDVI: {result['ndvi_mean']:.4f}")
        print(f"NDBI: {result['ndbi_mean']:.4f}")
        print(f"Cloud: {result['cloud_cover_pct']:.1f}%")

        out_rgb = Path("test_rgb.png")
        out_ndvi = Path("test_ndvi.png")
        out_rgb.write_bytes(result["rgb_png"])
        out_ndvi.write_bytes(result["ndvi_png"])
        print(f"Saved: {out_rgb} ({len(result['rgb_png'])} bytes)")
        print(f"Saved: {out_ndvi} ({len(result['ndvi_png'])} bytes)")
        if result.get("mapbox_png"):
            out_mapbox = Path("test_mapbox.png")
            out_mapbox.write_bytes(result["mapbox_png"])
            print(f"Saved: {out_mapbox} ({len(result['mapbox_png'])} bytes)")
        else:
            print("Mapbox: disabled (ENABLE_MAPBOX=false)")
