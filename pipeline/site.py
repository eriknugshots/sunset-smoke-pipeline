# pipeline/site.py — manifest generation and previous-cycle carryover planning.
#
# State model: the live GitHub Pages site IS the pipeline's state. Each run
# downloads whichever previous-cycle files it wants to keep (see
# plan_carryover), writes the new cycle's tiles alongside them, regenerates
# manifest.json (see build_manifest), then force-pushes one orphan commit so
# the Pages repo's history never grows.
import json
import re

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def cycle_id(cycle_iso):
    """'2026-07-27T12:00:00Z' -> '20260727t12z' (compact cycle directory name).

    Raises ValueError on anything that isn't a well-formed UTC ISO-8601
    timestamp — a malformed string here would otherwise silently produce a
    garbage-but-plausible-looking cycle id (see Task 8 -bin ambiguity for the
    cost of this kind of silent-garbage bug elsewhere in the pipeline).
    """
    if not _ISO_RE.match(cycle_iso):
        raise ValueError(f"cycle_iso must look like YYYY-MM-DDTHH:MM:SSZ, got {cycle_iso!r}")
    return cycle_iso[:10].replace("-", "") + "t" + cycle_iso[11:13] + "z"


def build_manifest(model, regions, now_iso, fires=None):
    """Serialize manifest.json contents (see plan for the target shape).

    `fires` is the NIFC marker list (client horizon labels only — regions are
    seeded from the smoke field itself, see pipeline/plumes.py); `kind` is
    home | conus | plume so the client can tell coverage tiers apart."""
    out = {"generatedAt": now_iso, "model": model, "fires": fires or [], "regions": []}
    for r in regions:
        cid = cycle_id(r["cycle"])
        out["regions"].append({"id": r["id"], "bounds": r["bounds"], "cycle": r["cycle"],
                               "cycleId": cid, "hours": r["hours"],
                               "kind": r.get("kind", "home"),
                               "path": f"tiles/{r['id']}/{cid}"})
    return json.dumps(out, indent=1)


def plan_carryover(prev_manifest, new_cycle_id):
    """Which previous-cycle region dirs to re-download into the new site.

    prev_manifest is the parsed manifest.json from the site as it stood
    before this run (or falsy if there was none, e.g. first-ever run).
    Returns a list of {"id", "path", "hours"} for every region whose cycleId
    differs from new_cycle_id — i.e. everything worth keeping around from
    the previous run. A region already on new_cycle_id needs no carryover
    (this run is about to (re)generate it fresh).
    """
    if not prev_manifest:
        return []
    plans = []
    for r in prev_manifest.get("regions", []):
        if r.get("cycleId") and r["cycleId"] != new_cycle_id:
            plans.append({"path": r["path"], "hours": r["hours"], "id": r["id"]})
    return plans
