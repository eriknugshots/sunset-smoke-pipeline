# pipeline/plumes.py — plume-region seeding from HRRR's own surface-smoke field.
#
# The original Plan-3 design seeded regions from the NIFC fire list and was
# replaced on Erik's direction (2026-07-27): the app must be air-quality driven,
# not fire driven. A fire 800 miles away can put a town at AQI 200-300 while
# nothing burns nearby; the fire list cannot see that, the smoke field can.
# Smoke is densest at its sources, so fires are still covered by construction.
import numpy as np
from pipeline.fires import haversine_km


def smoke_peaks(surf_ugm3, bounds, threshold_ugm3=10.0, limit=6, min_separation_km=500.0):
    """Greedy peak-pick over a (ny, nx) surface-concentration field (µg/m³).

    Cells are visited strongest-first; one is accepted if it is over the
    threshold and at least min_separation_km from every already-accepted peak
    (each peak becomes a 960 km box, so closer centres would waste a slot on
    overlapping coverage). The walk stops at the first below-threshold cell —
    everything after it in strongest-first order is below threshold too.
    """
    ny, nx = surf_ugm3.shape
    dlat = (bounds["north"] - bounds["south"]) / ny
    dlon = (bounds["east"] - bounds["west"]) / nx
    # Mask wgrib2's UNDEFINED fill (9.999e20, cells outside the model's native
    # domain) INSIDE the picker, so no caller can forget it: unmasked, the CONUS
    # box's ocean corners out-rank every real plume and eat all the slots
    # (observed on the first live multi-region run, 2026-07-28).
    surf_ugm3 = np.where(np.isfinite(surf_ugm3) & (surf_ugm3 < 1e19), surf_ugm3, 0.0)
    accepted = []
    for flat in np.argsort(surf_ugm3, axis=None)[::-1]:
        if len(accepted) >= limit:
            break
        v = float(surf_ugm3.flat[flat])
        if v < threshold_ugm3:
            break
        y, x = divmod(int(flat), nx)
        lat = bounds["south"] + (y + 0.5) * dlat
        lon = bounds["west"] + (x + 0.5) * dlon
        if all(haversine_km(lat, lon, a["lat"], a["lon"]) >= min_separation_km
               for a in accepted):
            accepted.append({"lat": lat, "lon": lon, "ugm3": v})
    return accepted


def region_id_for(lat, lon):
    """Stable id from the peak snapped to 1°, so a plume that drifts slightly
    keeps its id (and the client's cached tiles stay valid) instead of
    churning every cycle."""
    return f"plume-{round(lat)}n{abs(round(lon))}w"
