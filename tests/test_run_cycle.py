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

    calls = []

    def fake_process_hour(model, region, grid, cyc, fhr, out_dir, work):
        calls.append(fhr)
        if fhr == 1:
            raise RuntimeError("simulated f01 failure")

    monkeypatch.setattr(run_cycle, "process_hour", fake_process_hour)

    site_dir = tmp_path / "site"
    monkeypatch.setattr(sys, "argv", ["run_cycle.py", "--site-dir", str(site_dir), "--hours", "3"])
    run_cycle.main()

    assert calls == [0, 1, 2]
    manifest = json.loads((site_dir / "manifest.json").read_text())
    region = manifest["regions"][0]
    assert region["hours"] == 3


def test_early_exit_gated_on_completeness(tmp_path, monkeypatch):
    """A published manifest on the same cycleId but short of the full horizon
    (mid-upload HRRR cycle) must NOT freeze the run -- it should rebuild and
    top up. Only a manifest that already reports the full horizon early-exits."""
    monkeypatch.setattr(run_cycle, "ROOT", tmp_path)
    monkeypatch.setattr(run_cycle, "http", _idx_http)

    model = run_cycle.CFG["models"][run_cycle.CFG["active"]]
    cyc = run_cycle.latest_synoptic(model)
    cid = run_cycle.sitemod.cycle_id(cyc.strftime("%Y-%m-%dT%H:00:00Z"))
    expected = model["synopticHorizon"] + 1
    region_id = run_cycle.REGIONS[0]["id"]

    monkeypatch.setattr(run_cycle, "process_hour", lambda *a, **k: None)

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
