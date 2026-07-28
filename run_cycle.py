#!/usr/bin/env python3
"""Build SMK1 tiles for the latest synoptic HRRR cycle. See README.
Usage: run_cycle.py --site-dir site [--hours N] [--pages-base URL]"""
import argparse, concurrent.futures as cf, datetime, json, os, re, sys, urllib.error, urllib.request
from pathlib import Path
import numpy as np
from pipeline import idx as idxmod, grib, site as sitemod, observations as obsmod
from pipeline import fires as firesmod, plumes as plumesmod, history as historymod
from pipeline.encode import encode_smk1
from pipeline.tile import build_tile_arrays
import zlib

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "model-config.json").read_text())
_REGIONS_CFG = json.loads((ROOT / "regions.json").read_text())
REGIONS = _REGIONS_CFG["regions"]
PLUME_CFG = _REGIONS_CFG.get("plumeRegions", {})
MARKER_CFG = _REGIONS_CFG.get("markers", {})

def http(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()

def latest_synoptic(model, now=None):
    """Newest 00/06/12/18z cycle whose f01 idx exists (walk back up to 4 cycles)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cands = []
    for back_h in range(0, 30):
        t = now - datetime.timedelta(hours=back_h)
        if t.hour in model["synopticCycles"]:
            cands.append(t.replace(minute=0, second=0, microsecond=0))
    for t in cands[:4]:
        url = tile_url(model, t, 1) + ".idx"
        try:
            http(url, timeout=30)
            return t
        except urllib.error.URLError:   # HTTPError is a URLError subclass — covers both
            continue
    sys.exit("no available synoptic cycle found")

def tile_url(model, cyc, fhr, kind="natPath"):
    # Generic {fff:0Nd} substitution: HRRR uses 2-digit forecast hours, RRFS 3-digit.
    # (A literal .replace("{fff:02d}", ...) would leave the RRFS template unresolved
    # and break the cutover with a misleading "no cycle available" error.)
    path = model[kind].replace("{ymd}", cyc.strftime("%Y%m%d")).replace("{hh}", cyc.strftime("%H"))
    path = re.sub(r"\{fff:0(\d)d\}", lambda m: f"{fhr:0{int(m.group(1))}d}", path)
    return model["bucket"] + "/" + path

def rrfs_canary(models):
    try:
        m = models["rrfs"]
        cyc = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        http(tile_url(m, cyc, 1) + ".idx", timeout=20)
        print("::warning::RRFS AVAILABLE — smoke fields visible in the RRFS bucket; plan the cutover (model-config 'active').")
    except Exception:
        pass

def fetch_hour(model, cyc, fhr, work):
    """Download this forecast hour's MASSDEN+HGT records ONCE. Every region
    regrids from the returned file — regions differ only in their -new_grid
    arguments, so re-downloading per region would multiply bandwidth by the
    region count (8 after Plan 3: home + CONUS + up to 6 plume boxes)."""
    nat_url = tile_url(model, cyc, fhr)
    recs = idxmod.parse_idx(http(nat_url + ".idx").decode())
    sel_m = idxmod.select_records(recs, model["massdenVar"], "hybrid level", model["hybridLevels"])
    sel_h = idxmod.select_records(recs, model["hgtVar"], "hybrid level", model["hybridLevels"])
    if len(sel_m) < model["hybridLevels"] or len(sel_h) < model["hybridLevels"]:
        raise RuntimeError(f"f{fhr:02d}: incomplete records ({len(sel_m)} massden, {len(sel_h)} hgt)")
    gp = work / f"f{fhr:02d}.grib2"
    grib.fetch_ranges(nat_url, idxmod.byte_ranges(sel_m + sel_h, recs), gp)
    return gp

def tile_region(model, region, grid, gp, cyc, fhr, out_dir, work):
    """Regrid the already-downloaded hour onto one region's grid and write its
    SMK1 tile. Work-file names carry the region id: hours run concurrently and
    regions run within each hour, so bare f{fhr} names would collide."""
    bp = work / f"f{fhr:02d}_{region['id']}.bin"
    try:
        grib.regrid_to_bin(gp, grid, f":({model['massdenVar']}|{model['hgtVar']}):", bp)
        n = model["hybridLevels"]
        fields = grib.parse_bin(bp.read_bytes(), region["nx"], region["ny"], 2 * n)
    finally:
        bp.unlink(missing_ok=True)
    # wgrib2 preserves input record order (verified across a full 49-hour cycle,
    # 2026-07-27): massden levels 1..n then hgt 1..n, as fetch_ranges wrote them.
    mass, hgt = fields[:n], fields[n:]
    terr, dens = build_tile_arrays(mass, hgt, region["nz"], region["zStepM"])
    (out_dir / f"f{fhr:02d}.smk1").write_bytes(
        encode_smk1(grid["bounds"], region["nx"], region["ny"], region["nz"],
                    0.0, float(region["zStepM"]), 4.0, terr, dens))
    # HPBL sidecar (captured per spec §A.1; unused by client v1). It costs a
    # SECOND .idx fetch, byte-range download and wgrib2 pass per REGION-hour —
    # per-region duplication accepted to keep each region's directory
    # self-contained — so it stays opt-in via model-config "captureHpbl".
    if not CFG.get("captureHpbl"):
        return
    sgp = work / f"sfc{fhr:02d}_{region['id']}.grib2"
    sbp = work / f"sfc{fhr:02d}_{region['id']}.bin"
    try:
        sfc_url = tile_url(model, cyc, fhr, "sfcPath")
        srecs = idxmod.parse_idx(http(sfc_url + ".idx").decode())
        sel_p = idxmod.select_records(srecs, model["hpblVar"], "surface", 1)
        grib.fetch_ranges(sfc_url, idxmod.byte_ranges(sel_p, srecs), sgp)
        grib.regrid_to_bin(sgp, grid, f":{model['hpblVar']}:", sbp)
        hp = grib.parse_bin(sbp.read_bytes(), region["nx"], region["ny"], 1)[0]
        (out_dir / f"hpbl_f{fhr:02d}.bin").write_bytes(zlib.compress(hp.astype("<f4").tobytes(), 6))
    except Exception as e:
        print(f"::warning::HPBL {region['id']} f{fhr:02d} skipped: {e}")
    finally:
        sgp.unlink(missing_ok=True)
        sbp.unlink(missing_ok=True)

def build_regions(model, cyc, work):
    """Fixed regions (home + CONUS) from config, plus one 960 km box per
    surface-smoke peak in this cycle's f01 CONUS field. Seeding from the smoke
    field, not the NIFC fire list (Erik, 2026-07-27): smoke is densest at its
    sources so fires are still covered, but a plume parked over a town 800
    miles downwind attracts a box too — the fire list cannot see that.

    Costs one duplicate f01 download (~30 MB) before the hour loop refetches
    it — accepted for simplicity. Deterministic per cycle (same cycle -> same
    peaks -> same ids), which the completeness gate relies on. Seeding failure
    is NOT fatal: a cycle with only home + CONUS is far better than no cycle."""
    regions = [dict(r, kind=r.get("kind", "home")) for r in REGIONS]
    if not PLUME_CFG.get("enabled"):
        return regions
    conus = next((r for r in regions if r.get("kind") == "conus"), None)
    if conus is None:
        print("::warning::no conus region configured; plume seeding skipped")
        return regions
    try:
        grid = grib.region_grid_args(conus)
        gp = fetch_hour(model, cyc, 1, work)
        bp = work / "seed.bin"
        try:
            grib.regrid_to_bin(gp, grid, f":({model['massdenVar']}|{model['hgtVar']}):", bp)
            n = model["hybridLevels"]
            fields = grib.parse_bin(bp.read_bytes(), conus["nx"], conus["ny"], 2 * n)
        finally:
            gp.unlink(missing_ok=True); bp.unlink(missing_ok=True)
        surf = fields[0] * 1e9        # lowest hybrid level, kg/m³ -> µg/m³ at ~8 m AGL
        peaks = plumesmod.smoke_peaks(
            surf, grid["bounds"],
            threshold_ugm3=PLUME_CFG.get("thresholdUgm3", 10.0),
            limit=PLUME_CFG.get("limit", 6),
            min_separation_km=PLUME_CFG.get("minSeparationKm", 500.0))
    except Exception as e:
        print(f"::warning::plume seeding failed, fixed regions only: {e}")
        return regions
    taken = {r["id"] for r in regions}
    for pk in peaks:
        rid = plumesmod.region_id_for(pk["lat"], pk["lon"])
        if rid in taken:
            continue              # two peaks snapping to the same 1° cell -> keep the stronger
        taken.add(rid)
        regions.append({"id": rid, "kind": "plume",
                        "centerLat": pk["lat"], "centerLon": pk["lon"],
                        "widthKm": PLUME_CFG["widthKm"], "nx": PLUME_CFG["nx"],
                        "ny": PLUME_CFG["ny"], "nz": PLUME_CFG["nz"],
                        "zStepM": PLUME_CFG["zStepM"], "peakUgm3": round(pk["ugm3"], 1)})
    print(f"regions: {[r['id'] for r in regions]}")
    return regions

def marker_fires():
    """NIFC fire clusters for the client's horizon markers ONLY -- fires no
    longer drive region placement (regions follow the smoke itself). Failure
    costs markers, never coverage: returns [] and warns."""
    try:
        fires = firesmod.fetch_fires(min_acres=MARKER_CFG.get("minAcres", 1000))
        clusters = firesmod.cluster_fires(fires, merge_km=MARKER_CFG.get("mergeKm", 300.0))
        return [{"name": c["lead"], "lat": round(c["lat"], 3), "lon": round(c["lon"], 3),
                 "acres": round(c["acres"])} for c in clusters]
    except Exception as e:
        print(f"::warning::NIFC marker fetch failed: {e}")
        return []

def write_observations(site_dir, regions_out):
    """Fetch EPA AirNow ground-truth PM2.5 observations for the union of all
    built region bounds and write site_dir/obs.json. Never raises -- a fetch
    failure must not fail the whole cycle, so it's logged as a GitHub Actions
    warning and a well-formed (empty) obs.json is written instead, so the
    client always has something to fetch."""
    try:
        bounds = [r["bounds"] for r in regions_out]
        union = {
            "west": min(b["west"] for b in bounds),
            "south": min(b["south"] for b in bounds),
            "east": max(b["east"] for b in bounds),
            "north": max(b["north"] for b in bounds),
        }
        obs, obs_hour = obsmod.fetch_observations(opener=http)
        in_bounds = obsmod.observations_in_bounds(obs, union)
        payload = {
            "hour": obs_hour,
            "observations": [
                {"name": o["name"], "lat": round(o["lat"], 4), "lon": round(o["lon"], 4),
                 "ugm3": round(o["ugm3"], 1)}
                for o in in_bounds
            ],
        }
    except Exception as e:
        print(f"::warning::AirNow observations fetch failed: {e}")
        payload = {"hour": None, "observations": []}
    (site_dir / "obs.json").write_text(json.dumps(payload, indent=1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-dir", required=True)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--pages-base", default=None, help="live site base URL for early-exit + carryover")
    args = ap.parse_args()
    model = CFG["models"][CFG["active"]]
    rrfs_canary(CFG["models"])
    cyc = latest_synoptic(model)
    cyc_iso = cyc.strftime("%Y-%m-%dT%H:00:00Z")
    cid = sitemod.cycle_id(cyc_iso)
    expected_hours = model["synopticHorizon"] + 1
    # Region list BEFORE the early-exit gate: completeness must cover every
    # region this run would build (a newly appeared plume region correctly
    # forces a rebuild). Seeding is deterministic per cycle, so re-running a
    # published-complete cycle reconstructs the same list and still exits.
    # NOTE (Task 3): the real build_regions downloads f01 into `work` — keep
    # `work` mkdir'd before this call then, but leave site_dir untouched until
    # after the gate (an early exit must not create directories).
    work = ROOT / "work"
    work.mkdir(exist_ok=True)   # seeding downloads f01 here; site_dir stays untouched until after the gate
    ALL_REGIONS = build_regions(model, cyc, work)
    prev = None
    if args.pages_base:
        try:
            prev = json.loads(http(args.pages_base.rstrip("/") + "/manifest.json", timeout=30))
            if args.hours is None:
                prev_by_id = {r.get("id"): r for r in prev.get("regions", [])}
                # Only skip when every region is BOTH on this cycle AND complete
                # (hours >= full horizon). HRRR uploads a cycle progressively (f48
                # lands ~1.5-2h after cycle time), so a run that starts mid-upload
                # must NOT freeze the tail hours until the next synoptic cycle —
                # an incomplete published cycle gets rebuilt and topped up instead.
                complete = all(
                    prev_by_id.get(region["id"], {}).get("cycleId") == cid
                    and prev_by_id.get(region["id"], {}).get("hours", 0) >= expected_hours
                    for region in ALL_REGIONS
                )
                if complete:
                    print(f"cycle {cid} already published complete — nothing to do")
                    return
        except Exception:
            prev = None
    hours = args.hours if args.hours is not None else expected_hours
    site_dir = Path(args.site_dir)
    site_dir.mkdir(parents=True, exist_ok=True); work.mkdir(exist_ok=True)
    grids = {r["id"]: grib.region_grid_args(r) for r in ALL_REGIONS}
    out_dirs = {}
    for r in ALL_REGIONS:
        d = site_dir / "tiles" / r["id"] / cid
        d.mkdir(parents=True, exist_ok=True)
        out_dirs[r["id"]] = d

    # Hour-outer, region-inner: each hour's CONUS GRIB is fetched ONCE and every
    # region regrids from it. Hours stay concurrent — they are independent, and
    # the cycle is dominated by ~30 MB of byte-range download per hour (pure I/O
    # wait), so threads turn a serial ~40 min download pass into roughly the
    # slowest few hours. Regions within one hour run serially in that hour's
    # thread: regrids are ~6 s of subprocess each and overlap across hours.
    def hour_job(fhr):
        gp = fetch_hour(model, cyc, fhr, work)
        ok_ids = []
        try:
            for r in ALL_REGIONS:
                try:
                    tile_region(model, r, grids[r["id"]], gp, cyc, fhr, out_dirs[r["id"]], work)
                    ok_ids.append(r["id"])
                    print(f"{r['id']} f{fhr:02d} ok")
                except Exception as e:
                    print(f"::warning::{r['id']} f{fhr:02d} failed: {e}")
        finally:
            gp.unlink(missing_ok=True)
        return ok_ids

    workers = max(1, int(os.environ.get("CYCLE_WORKERS", "6")))
    results = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(hour_job, f): f for f in range(hours)}
        for fut in cf.as_completed(futures):
            fhr = futures[fut]
            try:
                results[fhr] = fut.result()
            except Exception as e:
                print(f"::warning::f{fhr:02d} download failed: {e}")
                results[fhr] = []

    regions_out = []
    for r in ALL_REGIONS:
        # hours is the fetch EXTENT (highest successful fhr + 1), not a count of
        # successes: the client tolerates interior gaps by design, so a failure
        # at e.g. f03 must not truncate the manifest before the later f48 that
        # actually built successfully.
        max_ok = max([f for f, ids in results.items() if r["id"] in ids], default=-1)
        regions_out.append({"id": r["id"], "bounds": grids[r["id"]]["bounds"],
                            "cycle": cyc_iso, "hours": max_ok + 1,
                            "kind": r.get("kind", "home")})
    # (The previous-cycle carryover lived here and was removed 2026-07-28. It
    # re-downloaded and republished the entire previous cycle — ~149 MB — but
    # build_manifest only ever describes THIS cycle's regions, so nothing the
    # client can read ever pointed at those files. Worse, the early-exit gate
    # skips the following hourly runs, so the dead copy survived the full 6 h
    # until the next flip. Dropping it is what makes room for a 48 h archive
    # inside a 0.5 GB budget. The archive below does NOT depend on it: it
    # sources the previous cycle from the live site, where that run published
    # it as its own current cycle.
    #
    # Accepted cost: a browser holding a manifest cached from just before a
    # flip can 404 on a few tiles for a few minutes. ensureSmokeHour already
    # swallows a failed hour and falls back to the nearest loaded one.)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Rolling past-hours archive. Must run BEFORE the force-push, while the
    # previous cycle's tiles are still reachable on the live site — that is the
    # only place the pre-cycle hours exist. Purely additive: a region whose
    # archive fetch fails keeps every hour that did land, and the manifest
    # lists exactly the hours that are actually on disk.
    historymod.write_archive(site_dir, regions_out, prev, cyc_iso, now_iso,
                             args.pages_base, lambda u: http(u, timeout=60))
    (site_dir / "manifest.json").write_text(
        sitemod.build_manifest(CFG["active"], regions_out, now_iso, marker_fires()))
    write_observations(site_dir, regions_out)
    (site_dir / ".nojekyll").write_text("")
    print(f"built cycle {cid}: {sum(r['hours'] for r in regions_out)} tiles")

if __name__ == "__main__":
    main()
