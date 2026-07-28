# tests/test_run_cycle.py — orchestrator-level contracts that don't need wgrib2
# or real network: manifest "hours" extent semantics, and the completeness-gated
# early-exit against a partially-published (mid-upload) HRRR cycle.
import json
import shutil
import sys

import run_cycle


def _idx_http(url, timeout=60):
    # Any well-formed idx line is enough for latest_synoptic()/rrfs_canary() to
    # treat the cycle as available; contents are never parsed in these tests.
    return b"1:0:d=2026072612:MASSDEN:1 hybrid level:6 hour fcst:\n"


def test_manifest_hours_is_extent_not_count(tmp_path, monkeypatch):
    """f01 fails out of a 3-hour run -> manifest reports hours==3 (extent),
    not 2 (count of successes) -- an interior gap must not truncate the
    manifest before a later hour that actually built successfully."""
    monkeypatch.setattr(run_cycle, "ROOT", tmp_path)
    monkeypatch.setattr(run_cycle, "http", _idx_http)
    # These tests pin the ORCHESTRATION contracts (manifest extent, completeness
    # gate) — one region and no plume seeding keeps them independent of how many
    # fixed regions the shipping config carries.
    monkeypatch.setattr(run_cycle, "REGIONS", run_cycle.REGIONS[:1])
    monkeypatch.setattr(run_cycle, "PLUME_CFG", {"enabled": False})

    calls = []

    # fetch-once semantics: fetch_hour succeeds (returns a path the caller may
    # unlink with missing_ok), tile_region fails for f01's only region — the
    # manifest-extent contract under test is identical to the old process_hour.
    monkeypatch.setattr(run_cycle, "fetch_hour",
                        lambda model, cyc, fhr, work: tmp_path / f"f{fhr:02d}.grib2")

    def fake_tile_region(model, region, grid, gp, cyc, fhr, out_dir, work):
        calls.append(fhr)
        if fhr == 1:
            raise RuntimeError("simulated f01 failure")

    monkeypatch.setattr(run_cycle, "tile_region", fake_tile_region)

    site_dir = tmp_path / "site"
    monkeypatch.setattr(sys, "argv", ["run_cycle.py", "--site-dir", str(site_dir), "--hours", "3"])
    run_cycle.main()

    # Every hour must be attempted, but NOT in a particular order: forecast hours
    # are independent and now build concurrently, so completion order varies run
    # to run. Asserting the exact sequence pinned the old serial implementation
    # and went flaky the moment it was parallelised.
    assert sorted(calls) == [0, 1, 2]
    manifest = json.loads((site_dir / "manifest.json").read_text())
    region = manifest["regions"][0]
    assert region["hours"] == 3


def test_early_exit_gated_on_completeness(tmp_path, monkeypatch):
    """A published manifest on the same cycleId but short of the full horizon
    (mid-upload HRRR cycle) must NOT freeze the run -- it should rebuild and
    top up. Only a manifest that already reports the full horizon early-exits."""
    monkeypatch.setattr(run_cycle, "ROOT", tmp_path)
    monkeypatch.setattr(run_cycle, "http", _idx_http)
    monkeypatch.setattr(run_cycle, "REGIONS", run_cycle.REGIONS[:1])
    monkeypatch.setattr(run_cycle, "PLUME_CFG", {"enabled": False})

    model = run_cycle.CFG["models"][run_cycle.CFG["active"]]
    cyc = run_cycle.latest_synoptic(model)
    cid = run_cycle.sitemod.cycle_id(cyc.strftime("%Y-%m-%dT%H:00:00Z"))
    expected = model["synopticHorizon"] + 1
    region_id = run_cycle.REGIONS[0]["id"]

    monkeypatch.setattr(run_cycle, "fetch_hour",
                        lambda model, cyc, fhr, work: tmp_path / f"f{fhr:02d}.grib2")
    monkeypatch.setattr(run_cycle, "tile_region", lambda *a, **k: None)

    def make_http(prev_hours):
        manifest_bytes = json.dumps(
            {"regions": [{"id": region_id, "cycleId": cid, "hours": prev_hours}]}
        ).encode()

        def fake_http(url, timeout=60):
            if url.endswith("/manifest.json"):
                return manifest_bytes
            return _idx_http(url, timeout)

        return fake_http

    site_dir = tmp_path / "site"
    monkeypatch.setattr(
        sys, "argv", ["run_cycle.py", "--site-dir", str(site_dir), "--pages-base", "https://example.test"]
    )

    # incomplete published cycle (mid-upload) -> run proceeds and rebuilds
    monkeypatch.setattr(run_cycle, "http", make_http(expected - 5))
    run_cycle.main()
    assert (site_dir / "manifest.json").exists()

    shutil.rmtree(site_dir)

    # complete published cycle -> early-exits before touching site_dir
    monkeypatch.setattr(run_cycle, "http", make_http(expected))
    run_cycle.main()
    assert not site_dir.exists()


def test_marker_fires_publishes_individual_fires_not_clusters(monkeypatch):
    """Every fire keeps its own name, acreage and true position.

    The clustered version collapsed a 300 km neighbourhood into one entry
    named after its heaviest member, with summed acreage at a weight-averaged
    centroid — near Bend that hid AKAWA BUTTE / Bench / GREEN MOUNTAIN /
    BREWER inside a single "0445 CROSSWHITE, 697k ac" sited 121 km away.
    """
    near_bend = [
        {"name": "0433 BREWER",     "lat": 44.32, "lon": -121.90, "acres": 70821.0, "contained": 10.0},
        {"name": "Bench",           "lat": 44.30, "lon": -120.60, "acres": 40296.0, "contained": 0.0},
        {"name": "0494 AKAWA BUTTE","lat": 44.40, "lon": -121.00, "acres": 27308.0, "contained": 0.0},
        {"name": "0611 GREEN MOUNTAIN","lat": 44.50, "lon": -121.20, "acres": 2136.0, "contained": 0.0},
        {"name": "0445 CROSSWHITE", "lat": 44.85, "lon": -120.47, "acres": 165883.0, "contained": 20.0},
    ]
    monkeypatch.setattr(run_cycle.firesmod, "fetch_fires", lambda **kw: list(near_bend))
    out = run_cycle.marker_fires()

    assert [f["name"] for f in out][0] == "0445 CROSSWHITE"      # biggest first
    assert {f["name"] for f in out} == {f["name"] for f in near_bend}, "no fire may be absorbed"
    by_name = {f["name"]: f for f in out}
    assert by_name["0433 BREWER"]["acres"] == 70821               # own acreage, not a cluster sum
    assert by_name["0433 BREWER"]["lat"] == 44.32                 # own position, not a centroid
    assert by_name["0433 BREWER"]["lon"] == -121.9
    assert all(f["acres"] < 200000 for f in out), "a summed-cluster acreage would exceed every real fire"


def test_marker_fires_caps_the_list_and_survives_a_feed_failure(monkeypatch):
    many = [{"name": f"f{i}", "lat": 40.0, "lon": -100.0, "acres": float(i + 1), "contained": 0.0}
            for i in range(500)]
    monkeypatch.setattr(run_cycle.firesmod, "fetch_fires", lambda **kw: many)
    out = run_cycle.marker_fires()
    assert len(out) == 200
    assert out[0]["name"] == "f499"        # biggest first, so the cap drops the smallest

    def boom(**kw):
        raise OSError("NIFC down")
    monkeypatch.setattr(run_cycle.firesmod, "fetch_fires", boom)
    assert run_cycle.marker_fires() == []   # markers are never worth failing a cycle over
