import json
import urllib.parse
import pytest
from pipeline.fires import (parse_fires, fire_weight, haversine_km, cluster_fires,
                             rank_clusters, region_id_for, fetch_fires)

PAYLOAD = {"features": [
    {"attributes": {"IncidentName": "CROSSWHITE", "IncidentSize": 159417.0, "PercentContained": 20.0},
     "geometry": {"x": -120.199, "y": 44.803}},
    {"attributes": {"IncidentName": "SHINGLE", "IncidentSize": 53976.0, "PercentContained": 0.0},
     "geometry": {"x": -119.897, "y": 44.469}},
    {"attributes": {"IncidentName": "Morrill", "IncidentSize": 642029.0, "PercentContained": 100.0},
     "geometry": {"x": -102.157, "y": 41.474}},
    {"attributes": {"IncidentName": "no geometry", "IncidentSize": 5000.0, "PercentContained": 0.0},
     "geometry": None},
]}

def test_parse_skips_rows_without_geometry():
    fires = parse_fires(PAYLOAD)
    assert [f["name"] for f in fires] == ["CROSSWHITE", "SHINGLE", "Morrill"]
    assert fires[0]["lat"] == 44.803 and fires[0]["lon"] == -120.199

def test_containment_discounts_weight():
    fires = {f["name"]: f for f in parse_fires(PAYLOAD)}
    # Morrill is 4x bigger than CROSSWHITE but fully contained -> must rank lower
    assert fire_weight(fires["Morrill"]) < fire_weight(fires["CROSSWHITE"])
    assert fire_weight(fires["SHINGLE"]) == pytest.approx(53976.0)   # 0% contained -> undiscounted

def test_haversine_known_distance():
    # Bend -> Crosswhite fire. Plan draft said "~100 km" but the correct great-circle
    # distance for these exact coordinates is ~121.3 km (cross-checked with an
    # independent haversine implementation) -- widened the window to match reality
    # rather than the plan's estimate.
    d = haversine_km(44.058, -121.315, 44.803, -120.199)
    assert 115 < d < 130

def test_cluster_merges_nearby_and_splits_far():
    fires = parse_fires(PAYLOAD)
    clusters = cluster_fires(fires, merge_km=300.0)
    assert len(clusters) == 2                       # the two Oregon fires merge; Nebraska stands alone
    lead = clusters[0]
    assert lead["members"] == 2
    assert 44.0 < lead["lat"] < 45.2 and -121.0 < lead["lon"] < -119.5   # weighted centre between them

def test_rank_clusters_caps_and_orders():
    fires = parse_fires(PAYLOAD)
    ranked = rank_clusters(cluster_fires(fires, merge_km=300.0), limit=1)
    assert len(ranked) == 1 and ranked[0]["members"] == 2   # Oregon pair outweighs contained Morrill

def test_region_id_is_stable_under_small_drift():
    assert region_id_for(44.62, -120.05) == region_id_for(44.71, -120.11)
    assert region_id_for(44.62, -120.05) == "fire-45n120w"
    assert region_id_for(44.62, -120.05) != region_id_for(41.47, -102.16)

def test_rank_clusters_separation_skips_near_duplicate_and_promotes_next():
    # All on the same meridian so haversine reduces to R * radians(dlat) exactly.
    # limit=2 (not 6) so the limit actually binds with only 3 candidate clusters --
    # otherwise a "truncate-to-limit before filtering" mutant survives undetected,
    # because the slot-freeing behaviour is never exercised.
    c1 = {"lat": 40.0, "lon": -120.0, "weight": 1_000_000.0}
    c2 = {"lat": 41.79966, "lon": -120.0, "weight": 500_000.0}   # ~200 km from c1
    c3 = {"lat": 48.0958, "lon": -120.0, "weight": 100_000.0}    # ~900 km from c1
    ranked = rank_clusters([c1, c2, c3], limit=2, min_separation_km=500.0)
    assert ranked == [c1, c3]   # c2 is a near-duplicate of c1 and is skipped;
                                 # c3 is far enough and gets promoted into the freed slot

def test_rank_clusters_accepts_at_exact_boundary_separation():
    # Two clusters placed ~500 km apart; using the EXACT haversine distance as the
    # threshold pins ">=" semantics regardless of float rounding -- a ">" mutant
    # would reject the second cluster since d > d is False. Live data has accepted
    # pairs at 509.0 km and 565.3 km, within 2% and 13% of the 500 km threshold, so
    # this boundary is not academic.
    c1 = {"lat": 40.0, "lon": -120.0, "weight": 1_000_000.0}
    c2 = {"lat": 44.4972, "lon": -120.0, "weight": 500_000.0}
    d = haversine_km(c1["lat"], c1["lon"], c2["lat"], c2["lon"])
    assert abs(d - 500.0) < 1.0   # sanity: lands near the pipeline's chosen threshold
    ranked = rank_clusters([c1, c2], limit=2, min_separation_km=d)
    assert ranked == [c1, c2]   # exactly at the boundary -> BOTH accepted

def test_rank_clusters_zero_separation_matches_original_behaviour():
    # Compare against a plain top-N-by-weight sort, NOT a bare rank_clusters(...) call --
    # the default min_separation_km is now 500.0 (see nitpick below), so the bare call
    # would no longer represent "original" (pre-amendment) behaviour.
    fires = parse_fires(PAYLOAD)
    clusters = cluster_fires(fires, merge_km=300.0)
    top_by_weight = sorted(clusters, key=lambda c: c["weight"], reverse=True)[:6]
    assert rank_clusters(clusters, limit=6, min_separation_km=0.0) == top_by_weight

def test_containment_floor_prevents_zero_weight():
    # Without max(0.05, ...) a 100%-contained fire has weight 0. In cluster_fires
    # that would make wsum=0, trip the "or 1.0" fallback, and collapse the
    # weighted centre to (0, 0) -- a region box at null island.
    fully_contained = {"name": "Out", "lat": 40.0, "lon": -120.0,
                        "acres": 200_000.0, "contained": 100.0}
    assert fire_weight(fully_contained) == pytest.approx(200_000.0 * 0.05)

def test_fetch_fires_raises_on_error_payload():
    def opener(url, timeout):
        return json.dumps({"error": {"code": 400, "message": "bad request"}}).encode()
    with pytest.raises(RuntimeError):
        fetch_fires(opener=opener)

def test_fetch_fires_min_acres_reaches_where_clause():
    captured = {}
    def opener(url, timeout):
        captured["url"] = url
        return json.dumps({"features": []}).encode()
    result = fetch_fires(min_acres=5000, opener=opener)
    query = urllib.parse.unquote_plus(captured["url"])
    assert "IncidentSize > 5000" in query
    assert result == []
