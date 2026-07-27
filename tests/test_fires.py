import math
import pytest
from pipeline.fires import parse_fires, fire_weight, haversine_km, cluster_fires, rank_clusters, region_id_for

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
