#!/usr/bin/env python3
# tick.py — does the live site need a build? Exit 0 = yes (start build-tiles),
# 3 = no. The tick workflow calls it every 10 minutes for hours: GitHub's own
# schedule for build-tiles.yml ("hourly") fired only ~4 times a day at uneven
# times (2026-10-31..11-04), so the published smoke was often 7-11 h old.
#
# Builds a cycle once HRRR has uploaded its LAST forecast hour (~1.5-2 h after
# cycle time): the previous cycle still covers 48 h, so waiting costs nothing
# and saves publishing a short cycle only to rebuild it 30 min later. After
# FINAL_GRACE without the last hour, it builds what is there (run_cycle tops a
# short cycle up on a later run, as before).
import argparse
import datetime
import json
import sys
import urllib.error

import run_cycle
from pipeline import site as sitemod

NEEDED, NOTHING = 0, 3
FINAL_GRACE = datetime.timedelta(hours=3)


def needs_build(manifest, cyc, expected_hours, final_ready, now):
    """Pure. `cyc` = newest available synoptic cycle (None: none found)."""
    if cyc is None:
        return False
    cid = sitemod.cycle_id(cyc.strftime("%Y-%m-%dT%H:00:00Z"))
    regions = (manifest or {}).get("regions") or []
    complete = bool(regions) and all(
        r.get("cycleId") == cid and r.get("hours", 0) >= expected_hours for r in regions)
    if complete:
        return False
    if not regions:
        return True
    return final_ready or now - cyc > FINAL_GRACE


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pages-base", required=True)
    a = p.parse_args()
    model = run_cycle.CFG["models"][run_cycle.CFG["active"]]
    try:
        cyc = run_cycle.latest_synoptic(model)
    except SystemExit:
        cyc = None
    try:
        manifest = json.loads(run_cycle.http(a.pages_base.rstrip("/") + "/manifest.json", timeout=30))
    except Exception:
        manifest = None
    final_ready = False
    if cyc is not None:
        try:
            run_cycle.http(run_cycle.tile_url(model, cyc, model["synopticHorizon"]) + ".idx", timeout=30)
            final_ready = True
        except urllib.error.URLError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    need = needs_build(manifest, cyc, model["synopticHorizon"] + 1, final_ready, now)
    print(f"newest cycle {cyc and cyc.strftime('%Y-%m-%d %HZ')}, last hour {'up' if final_ready else 'not up'}: "
          + ("build" if need else "site is current"))
    sys.exit(NEEDED if need else NOTHING)


if __name__ == "__main__":
    main()
