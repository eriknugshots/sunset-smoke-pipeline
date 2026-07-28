# pipeline/history.py — the rolling past-hours archive.
#
# WHY THIS EXISTS. A published cycle covers cycle_time .. cycle_time+48h and
# nothing before it. HRRR synoptic cycles are 6-hourly, so at any moment a
# chunk of "today" is EARLIER than the newest cycle and has no tile at all.
# The client could only clamp to forecast hour 0 across that gap, which Erik
# reported on 2026-07-27 as "the smoke just stays the same from midnight until
# 3 pm" — a frozen morning under an AQI number that was visibly changing.
#
# Every one of those hours WAS published at some point, as a short-lead
# forecast of an earlier cycle; the orphan force-push simply threw it away.
# This module keeps the last WINDOW_H of them in tiles/<region>/history/,
# one file per UTC hour, so the client has real hour-by-hour past smoke.
#
# It downloads nothing from NOAA. The archive is assembled entirely from
# tiles this pipeline has already produced and published, in priority order:
#   1. an existing history entry (archived by an earlier run) — carried forward
#   2. the previous cycle's forecast tile for that hour
# Because (2) only reaches back to the previous cycle time, the archive fills
# in at roughly one synoptic cycle (~6 h) per run and reaches its full 48 h
# after about two days of running. It never regresses once filled.
import datetime
from pathlib import Path

WINDOW_H = 48
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def parse_iso(iso):
    """'2026-07-27T12:00:00Z' -> aware UTC datetime. Raises on anything else."""
    return datetime.datetime.strptime(iso, _ISO).replace(tzinfo=datetime.timezone.utc)


def format_iso(dt):
    return dt.strftime(_ISO)


def hour_id(when):
    """Hour-resolution file stem: '2026-07-27T12:00:00Z' -> '20260727t12z'.

    Deliberately the same shape as site.cycle_id so a history filename and a
    cycle directory name read alike, and the client can build either with one
    formatter."""
    dt = parse_iso(when) if isinstance(when, str) else when
    return dt.strftime("%Y%m%dt%Hz")


def history_path(region_id):
    return f"tiles/{region_id}/history"


def plan_history(prev_manifest, region_id, cycle_iso, now_iso, window_h=WINDOW_H):
    """Which past hours the new site should carry for one region, and where
    each file comes from on the CURRENT live site.

    Returns [{"t": iso, "src": site-relative path, "dst": filename}] ordered
    oldest -> newest. Covers [now-window_h, cycle_time): hours from the cycle
    onward are served by the cycle directory itself, so archiving them too
    would duplicate storage for no gain.

    prev_manifest is the site's manifest as it stood BEFORE this run (falsy on
    a first run, which correctly yields whatever the previous cycle can cover
    and nothing more)."""
    cyc = parse_iso(cycle_iso)
    now = parse_iso(now_iso).replace(minute=0, second=0, microsecond=0)
    start = now - datetime.timedelta(hours=window_h)
    end = min(cyc, now + datetime.timedelta(hours=1))

    prev_r = next((r for r in (prev_manifest or {}).get("regions", [])
                   if r.get("id") == region_id), None)
    archived = set((prev_r or {}).get("history") or [])
    # A region archived before historyPath existed still has a predictable
    # location — don't drop its whole archive over a missing key.
    prev_hist_dir = (prev_r or {}).get("historyPath") or history_path(region_id)

    plans, t = [], start
    while t < end:
        iso, src = format_iso(t), None
        if iso in archived:
            src = f"{prev_hist_dir}/{hour_id(t)}.smk1"
        elif prev_r and prev_r.get("cycle") and prev_r.get("path"):
            # Short-lead forecast from the cycle that was live before this one.
            fhr = int((t - parse_iso(prev_r["cycle"])).total_seconds() // 3600)
            if 0 <= fhr < prev_r.get("hours", 0):
                src = f"{prev_r['path']}/f{fhr:02d}.smk1"
        if src:
            plans.append({"t": iso, "src": src, "dst": f"{hour_id(t)}.smk1"})
        t += datetime.timedelta(hours=1)
    return plans


def write_archive(site_dir, regions_out, prev_manifest, cycle_iso, now_iso,
                  base_url, fetch, window_h=WINDOW_H, log=print):
    """Populate tiles/<region>/history/ for every region and record what landed.

    Mutates each entry of `regions_out` in place, setting "history" to the
    ascending list of UTC hour stamps that ACTUALLY have a file on disk — the
    manifest must never advertise an hour the client would then 404 on.

    `fetch(url) -> bytes` is injected so this is testable without network, and
    so a single hour's failure stays survivable: the archive is additive, and
    a gap costs one hour of scrub, not the run."""
    if not base_url:
        return
    base = base_url.rstrip("/")
    for r in regions_out:
        hist_dir = Path(site_dir) / history_path(r["id"])
        kept = []
        for p in plan_history(prev_manifest, r["id"], cycle_iso, now_iso, window_h):
            try:
                data = fetch(f"{base}/{p['src']}")
            except Exception:
                continue
            hist_dir.mkdir(parents=True, exist_ok=True)
            (hist_dir / p["dst"]).write_bytes(data)
            kept.append(p["t"])
        if kept:
            r["history"] = kept
            log(f"{r['id']} history: {len(kept)} h ({kept[0]} .. {kept[-1]})")
