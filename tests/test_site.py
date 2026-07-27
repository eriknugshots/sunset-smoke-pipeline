import json
from pathlib import Path

import pytest

from pipeline.site import cycle_id, build_manifest, plan_carryover


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


def test_plan_carryover_keeps_only_previous_cycle():
    prev = {"regions": [{"id": "central-oregon", "cycleId": "20260727t06z",
                         "path": "tiles/central-oregon/20260727t06z", "hours": 49}]}
    plan = plan_carryover(prev, new_cycle_id="20260727t12z")
    assert ("tiles/central-oregon/20260727t06z", 49) in [(p["path"], p["hours"]) for p in plan]
    assert plan_carryover(prev, new_cycle_id="20260727t06z") == []   # same cycle -> nothing to carry
