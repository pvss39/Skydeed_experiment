"""
change_detector.py — Compare current vs baseline spectral indices.
No ML: simple threshold logic. Works. Ship it.
"""

import math

# Telugu translations for alert types
_ALERT_TE = {
    "encroachment":    "ఆక్రమణ గుర్తించబడింది",
    "construction":    "నిర్మాణం కనుగొనబడింది",
    "vegetation_loss": "వృక్షసంపద తగ్గుదల",
    "flooding":        "వరద / నీటి ముంపు",
}

_ALERT_EN = {
    "encroachment":    "Encroachment detected",
    "construction":    "Construction / land conversion detected",
    "vegetation_loss": "Significant vegetation loss",
    "flooding":        "Flooding or waterlogging detected",
}


def detect_changes(
    current_ndvi: float,
    baseline_ndvi: float,
    current_ndbi: float = None,
    baseline_ndbi: float = None,
    current_mndwi: float = None,
    baseline_mndwi: float = None,
    threshold: float = 0.15,
) -> dict:
    """
    Compare current vs baseline indices. Return alert dict.

    Returns:
        alert          : bool
        alert_type     : "encroachment"|"construction"|"flooding"|"vegetation_loss"|None
        ndvi_change_pct: float  (positive = improvement, negative = loss)
        ndbi_change_pct: float | None
        confidence     : float  (0–1)
        description_en : str
        description_te : str
    """
    # Sanitise — SAR fallback may give NaN
    ndvi_valid = _valid(current_ndvi) and _valid(baseline_ndvi)
    ndbi_valid = (current_ndbi is not None and baseline_ndbi is not None
                  and _valid(current_ndbi) and _valid(baseline_ndbi))
    mndwi_valid = (current_mndwi is not None and baseline_mndwi is not None
                   and _valid(current_mndwi) and _valid(baseline_mndwi))

    ndvi_change_pct = _pct_change(current_ndvi, baseline_ndvi) if ndvi_valid else 0.0
    ndbi_change_pct = _pct_change(current_ndbi, baseline_ndbi) if ndbi_valid else None
    mndwi_change_pct = _pct_change(current_mndwi, baseline_mndwi) if mndwi_valid else None

    # --- Decision logic (order matters) ---
    alert_type = None
    confidence = 0.0
    signals = []

    ndvi_drop = ndvi_valid and ndvi_change_pct < -threshold * 100
    ndbi_rise = ndbi_valid and ndbi_change_pct > threshold * 100
    water_rise = mndwi_valid and mndwi_change_pct > threshold * 100

    if water_rise:
        alert_type = "flooding"
        confidence = min(1.0, abs(mndwi_change_pct) / 50)
        signals.append("MNDWI increased")

    elif ndvi_drop and ndbi_rise:
        alert_type = "encroachment"
        confidence = min(1.0, (abs(ndvi_change_pct) + abs(ndbi_change_pct)) / 100)
        signals.append("NDVI dropped + NDBI rose")

    elif ndbi_rise and not ndvi_drop:
        alert_type = "construction"
        confidence = min(1.0, abs(ndbi_change_pct) / 50)
        signals.append("NDBI rose without vegetation loss")

    elif ndvi_drop:
        alert_type = "vegetation_loss"
        confidence = min(1.0, abs(ndvi_change_pct) / 50)
        signals.append("NDVI dropped")

    alert = alert_type is not None

    # Build descriptions
    if alert:
        base_en = _ALERT_EN[alert_type]
        base_te = _ALERT_TE[alert_type]
        detail_en = (
            f"{base_en}. "
            f"NDVI change: {ndvi_change_pct:+.1f}%"
            + (f", NDBI change: {ndbi_change_pct:+.1f}%" if ndbi_change_pct is not None else "")
            + f". Confidence: {confidence*100:.0f}%."
        )
        detail_te = (
            f"{base_te}. "
            f"NDVI మార్పు: {ndvi_change_pct:+.1f}%"
            + (f", NDBI మార్పు: {ndbi_change_pct:+.1f}%" if ndbi_change_pct is not None else "")
            + f". విశ్వాసం: {confidence*100:.0f}%."
        )
    else:
        detail_en = (
            f"No significant change detected. "
            f"NDVI: {current_ndvi:.3f} (baseline {baseline_ndvi:.3f})."
            if ndvi_valid else "Spectral data unavailable (SAR pass only)."
        )
        detail_te = (
            f"పెద్దగా మార్పు లేదు. "
            f"NDVI: {current_ndvi:.3f} (ప్రారంభం {baseline_ndvi:.3f})."
            if ndvi_valid else "దృశ్యమాన డేటా లేదు (SAR మాత్రమే)."
        )

    return {
        "alert": alert,
        "alert_type": alert_type,
        "ndvi_change_pct": ndvi_change_pct,
        "ndbi_change_pct": ndbi_change_pct,
        "mndwi_change_pct": mndwi_change_pct,
        "confidence": confidence,
        "description_en": detail_en,
        "description_te": detail_te,
        "signals": signals,
    }


def _pct_change(current: float, baseline: float) -> float:
    """Percentage change relative to baseline."""
    if baseline == 0:
        return 0.0
    return ((current - baseline) / abs(baseline)) * 100


def _valid(v: float) -> bool:
    return v is not None and not math.isnan(v) and not math.isinf(v)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        # (label, cur_ndvi, base_ndvi, cur_ndbi, base_ndbi)
        ("Healthy farm", 0.65, 0.63, -0.2, -0.21),
        ("Vegetation loss", 0.40, 0.65, -0.2, -0.21),
        ("Construction", 0.60, 0.62, 0.10, -0.15),
        ("Encroachment (both)", 0.38, 0.65, 0.12, -0.15),
        ("Flooding", 0.50, 0.55, -0.1, -0.12),
    ]
    for label, cn, bn, cndbi, bndbi in tests:
        mndwi_args = {}
        if label == "Flooding":
            mndwi_args = {"current_mndwi": 0.3, "baseline_mndwi": 0.05}
        res = detect_changes(cn, bn, cndbi, bndbi, **mndwi_args)
        print(f"\n[{label}]")
        print(f"  alert={res['alert']} type={res['alert_type']} conf={res['confidence']:.2f}")
        print(f"  EN: {res['description_en']}")
        print(f"  TE: {res['description_te']}")
