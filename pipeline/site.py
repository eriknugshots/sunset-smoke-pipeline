# pipeline/site.py — manifest generation.
#
# State model: the live GitHub Pages site IS the pipeline's state. Each run
# reads the published manifest, downloads the past hours worth keeping (see
# pipeline/history.py), writes the new cycle's tiles alongside them,
# regenerates manifest.json (see build_manifest), then force-pushes one orphan
# commit so the Pages repo's history never grows.
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
        entry = {"id": r["id"], "bounds": r["bounds"], "cycle": r["cycle"],
                 "cycleId": cid, "hours": r["hours"],
                 "kind": r.get("kind", "home"),
                 "path": f"tiles/{r['id']}/{cid}"}
        # Past-hours archive (pipeline/history.py): UTC hour stamps, ascending,
        # every one of which has a file at historyPath/<hour_id>.smk1. Absent
        # on a first run and on any region whose archive came up empty — the
        # client must treat it as optional.
        if r.get("history"):
            entry["history"] = list(r["history"])
            entry["historyPath"] = f"tiles/{r['id']}/history"
        out["regions"].append(entry)
    return json.dumps(out, indent=1)


# plan_carryover() was removed 2026-07-28. It selected the previous cycle's
# region dirs to re-download into the new site, but build_manifest only ever
# describes the CURRENT cycle, so nothing the client reads ever referenced
# them — ~149 MB of unreachable files per cycle. The past hours that were
# actually worth keeping now live in the rolling archive (pipeline/history.py),
# which indexes them properly.
