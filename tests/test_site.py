import json
from pathlib import Path

import pytest

from pipeline.site import cycle_id, build_manifest


def test_cycle_id():
    assert cycle_id("2026-07-27T12:00:00Z") == "20260727t12z"


def test_cycle_id_rejects_malformed_iso():
    with pytest.raises(ValueError):
        cycle_id("2026-07-27 12:00")


def test_build_manifest_shape():
    m = json.loads(build_manifest("hrrr", [{"id": "central-oregon",
        "bounds": {"west": -122.8, "south": 41.9, "east": -119.8, "north": 46.2},
        "cycle": "2026-07-27T12:00:00Z", "hours": 49}], now_iso="2026-07-27T13:22:00Z"))
    r = m["regions"][0]
    assert r["cycleId"] == "20260727t12z" and r["path"].endswith("/20260727t12z")
    assert m["generatedAt"] == "2026-07-27T13:22:00Z"



def test_manifest_carries_fires_and_kind():
    m = json.loads(build_manifest("hrrr", [{"id": "plume-45n120w", "kind": "plume",
        "bounds": {"west": -126.0, "south": 40.0, "east": -114.0, "north": 49.0},
        "cycle": "2026-07-27T12:00:00Z", "hours": 49}], now_iso="2026-07-27T13:22:00Z",
        fires=[{"name": "CROSSWHITE", "lat": 44.8, "lon": -120.2, "acres": 159417}]))
    assert m["regions"][0]["kind"] == "plume"
    assert m["fires"][0]["name"] == "CROSSWHITE"
    assert json.loads(build_manifest("hrrr", [], now_iso="x"))["fires"] == []


def test_manifest_publishes_the_history_archive():
    base = {"id": "home", "bounds": {"west": -1.0, "south": 1.0, "east": 2.0, "north": 3.0},
            "cycle": "2026-07-27T12:00:00Z", "hours": 49}
    m = json.loads(build_manifest("hrrr", [dict(base, history=["2026-07-27T06:00:00Z",
                                                               "2026-07-27T07:00:00Z"])],
                                  now_iso="2026-07-27T13:22:00Z"))
    r = m["regions"][0]
    assert r["history"] == ["2026-07-27T06:00:00Z", "2026-07-27T07:00:00Z"]
    assert r["historyPath"] == "tiles/home/history"


def test_manifest_omits_history_keys_when_the_archive_is_empty():
    # The client treats these as optional; emitting an empty list would make a
    # region with no archive look like one whose archive failed to load.
    r = json.loads(build_manifest("hrrr", [{"id": "home", "bounds": {}, "hours": 49,
        "cycle": "2026-07-27T12:00:00Z"}], now_iso="x"))["regions"][0]
    assert "history" not in r and "historyPath" not in r
