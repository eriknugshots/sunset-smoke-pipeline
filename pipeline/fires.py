# pipeline/fires.py — active large-fire discovery and clustering (NIFC WFIGS feed).
# Field names verified live 2026-07-27: IncidentSize (acres), PercentContained,
# IncidentTypeCategory='WF', FireOutDateTime IS NULL for still-burning incidents.
# Geometry arrives as {"x": lon, "y": lat} when outSR=4326 is requested.
import json, math, urllib.parse, urllib.request

NIFC_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
            "WFIGS_Incident_Locations_Current/FeatureServer/0/query")
EARTH_R_KM = 6371.0


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def fetch_fires(min_acres=1000, timeout=60, opener=None):
    """Query the live feed. `opener(url, timeout) -> bytes` is injectable for tests."""
    params = {
        "where": (f"IncidentSize > {min_acres} AND FireOutDateTime IS NULL "
                  "AND IncidentTypeCategory='WF'"),
        "outFields": "IncidentName,IncidentSize,PercentContained",
        "returnGeometry": "true", "outSR": "4326", "f": "json",
        "orderByFields": "IncidentSize DESC", "resultRecordCount": "200",
    }
    raw = (opener or _get)(NIFC_URL + "?" + urllib.parse.urlencode(params), timeout)
    payload = json.loads(raw)
    if "error" in payload:
        raise RuntimeError(f"NIFC query failed: {payload['error']}")
    return parse_fires(payload)


def parse_fires(payload):
    """ArcGIS FeatureSet -> [{name, lat, lon, acres, contained}]; rows without geometry are skipped."""
    out = []
    for feat in payload.get("features", []):
        attrs = feat.get("attributes") or {}
        geom = feat.get("geometry") or {}
        lat, lon, acres = geom.get("y"), geom.get("x"), attrs.get("IncidentSize")
        if lat is None or lon is None or not acres:
            continue
        contained = attrs.get("PercentContained")
        out.append({
            "name": (attrs.get("IncidentName") or "fire").strip(),
            "lat": float(lat), "lon": float(lon), "acres": float(acres),
            "contained": float(contained) if contained is not None else 0.0,
        })
    return out


def fire_weight(f):
    """Acres discounted by containment — a fully contained burn scar is not a smoke source.
    Floored at 5% so a contained megafire still outranks nothing at all."""
    return f["acres"] * max(0.05, 1.0 - f["contained"] / 100.0)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))


def cluster_fires(fires, merge_km=300.0):
    """Greedy single-pass clustering: the heaviest unassigned fire seeds a cluster and
    absorbs every unassigned fire within merge_km of that seed. Centre is weight-averaged.
    Greedy (not full single-link) on purpose — it keeps clusters near a dominant fire
    instead of chaining across a whole state."""
    remaining = sorted(fires, key=fire_weight, reverse=True)
    clusters = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        rest = []
        for f in remaining:
            (group if haversine_km(seed["lat"], seed["lon"], f["lat"], f["lon"]) <= merge_km
             else rest).append(f)
        remaining = rest
        wsum = sum(fire_weight(f) for f in group) or 1.0
        clusters.append({
            "lat": sum(f["lat"] * fire_weight(f) for f in group) / wsum,
            "lon": sum(f["lon"] * fire_weight(f) for f in group) / wsum,
            "weight": wsum,
            "members": len(group),
            "acres": sum(f["acres"] for f in group),
            "lead": max(group, key=fire_weight)["name"],
            "fires": group,
        })
    return clusters


def rank_clusters(clusters, limit=6, min_separation_km=500.0):
    """Heaviest-first selection, with an optional minimum separation between accepted
    cluster centres. Each cluster becomes a 960 km wide fire box, so centres closer
    than roughly half that (~500 km) already overlap more than 50% -- wasting a
    pre-warm slot on redundant coverage of the same area instead of a distinct one.
    Defaults to 500.0 so a bare call gets this designed behaviour; pass 0.0 to
    recover the original top-N-by-weight-only selection.

    Walks the weight-sorted list and accepts a cluster only if it is at least
    `min_separation_km` from every already-accepted cluster, continuing down the
    list until `limit` slots are filled: a rejected near-duplicate frees its slot
    for the next sufficiently-distant cluster rather than shrinking the result.
    `min_separation_km=0.0` disables the filter and reproduces the original
    top-N-by-weight behaviour exactly.
    """
    ordered = sorted(clusters, key=lambda c: c["weight"], reverse=True)
    if min_separation_km <= 0:
        return ordered[:limit]
    accepted = []
    for c in ordered:
        if len(accepted) >= limit:
            break
        if all(haversine_km(c["lat"], c["lon"], a["lat"], a["lon"]) >= min_separation_km
               for a in accepted):
            accepted.append(c)
    return accepted


def region_id_for(lat, lon):
    """Stable id from the cluster centre snapped to 1°, so a drifting cluster keeps its
    id (and the client's cached tiles stay valid) instead of churning every cycle."""
    return f"fire-{round(lat)}n{abs(round(lon))}w"
