#!/usr/bin/env python3
"""Build SMK1 tiles for the latest synoptic HRRR cycle. See README.
Usage: run_cycle.py --site-dir site [--hours N] [--pages-base URL]"""
import argparse, concurrent.futures as cf, datetime, json, os, re, sys, urllib.error, urllib.request
from pathlib import Path
import numpy as np
from pipeline import idx as idxmod, grib, site as sitemod, observations as obsmod
from pipeline.encode import encode_smk1
from pipeline.tile import build_tile_arrays
import zlib

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "model-config.json").read_text())
REGIONS = json.loads((ROOT / "regions.json").read_text())["regions"]

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

def process_hour(model, region, grid, cyc, fhr, out_dir, work):
    nat_url = tile_url(model, cyc, fhr)
    recs = idxmod.parse_idx(http(nat_url + ".idx").decode())
    sel_m = idxmod.select_records(recs, model["massdenVar"], "hybrid level", model["hybridLevels"])
    sel_h = idxmod.select_records(recs, model["hgtVar"], "hybrid level", model["hybridLevels"])
    if len(sel_m) < model["hybridLevels"] or len(sel_h) < model["hybridLevels"]:
        raise RuntimeError(f"f{fhr:02d}: incomplete records ({len(sel_m)} massden, {len(sel_h)} hgt)")
    gp, bp = work / f"f{fhr:02d}.grib2", work / f"f{fhr:02d}.bin"
    try:
        grib.fetch_ranges(nat_url, idxmod.byte_ranges(sel_m + sel_h, recs), gp)
        grib.regrid_to_bin(gp, grid, f":({model['massdenVar']}|{model['hgtVar']}):", bp)
        n = model["hybridLevels"]
        fields = grib.parse_bin(bp.read_bytes(), region["nx"], region["ny"], 2 * n)
    finally:
        gp.unlink(missing_ok=True)
        bp.unlink(missing_ok=True)
    # wgrib2 preserves input record order: our ranges were massden levels 1..n then hgt 1..n.
    # VERIFY in Task 8 with -s inventory; if wgrib2 reorders, split into two -match passes.
    mass, hgt = fields[:n], fields[n:]
    terr, dens = build_tile_arrays(mass, hgt, region["nz"], region["zStepM"])
    (out_dir / f"f{fhr:02d}.smk1").write_bytes(
        encode_smk1(grid["bounds"], region["nx"], region["ny"], region["nz"],
                    0.0, float(region["zStepM"]), 4.0, terr, dens))
    # HPBL sidecar (captured per spec §A.1; unused by client v1). It costs a
    # SECOND .idx fetch, byte-range download and wgrib2 pass per forecast hour —
    # a large slice of the cycle's runtime for a field nothing reads yet, so it is
    # opt-in via model-config "captureHpbl".
    if not CFG.get("captureHpbl"):
        return
    sgp, sbp = work / f"sfc{fhr:02d}.grib2", work / f"sfc{fhr:02d}.bin"
    try:
        sfc_url = tile_url(model, cyc, fhr, "sfcPath")
        srecs = idxmod.parse_idx(http(sfc_url + ".idx").decode())
        sel_p = idxmod.select_records(srecs, model["hpblVar"], "surface", 1)
        grib.fetch_ranges(sfc_url, idxmod.byte_ranges(sel_p, srecs), sgp)
        grib.regrid_to_bin(sgp, grid, f":{model['hpblVar']}:", sbp)
        hp = grib.parse_bin(sbp.read_bytes(), region["nx"], region["ny"], 1)[0]
        (out_dir / f"hpbl_f{fhr:02d}.bin").write_bytes(zlib.compress(hp.astype("<f4").tobytes(), 6))
    except Exception as e:
        print(f"::warning::HPBL f{fhr:02d} skipped: {e}")
    finally:
        sgp.unlink(missing_ok=True)
        sbp.unlink(missing_ok=True)

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
                    for region in REGIONS
                )
                if complete:
                    print(f"cycle {cid} already published complete — nothing to do")
                    return
        except Exception:
            prev = None
    hours = args.hours if args.hours is not None else expected_hours
    site_dir = Path(args.site_dir); work = ROOT / "work"
    site_dir.mkdir(parents=True, exist_ok=True); work.mkdir(exist_ok=True)
    regions_out = []
    for region in REGIONS:
        grid = grib.region_grid_args(region)
        out_dir = site_dir / "tiles" / region["id"] / cid
        out_dir.mkdir(parents=True, exist_ok=True)
        max_ok = -1
        # Forecast hours are independent, and the cycle is dominated by ~30 MB of
        # byte-range download per hour — pure I/O wait. Running them concurrently
        # turns a serial ~40 min cycle into roughly the slowest few hours. Threads
        # (not processes) are right here: urllib blocks on the socket, wgrib2 is a
        # subprocess, and numpy releases the GIL, so all three overlap.
        workers = max(1, int(os.environ.get("CYCLE_WORKERS", "6")))
        results = {}
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_hour, model, region, grid, cyc, f, out_dir, work): f
                       for f in range(hours)}
            for fut in cf.as_completed(futures):
                fhr = futures[fut]
                try:
                    fut.result()
                    results[fhr] = True
                    print(f"{region['id']} f{fhr:02d} ok")
                except Exception as e:
                    print(f"::warning::{region['id']} f{fhr:02d} failed: {e}")
        max_ok = max([f for f, ok in results.items() if ok], default=-1)
        # hours is the fetch EXTENT (highest successful fhr + 1), not a count of
        # successes: the client tolerates interior gaps by design, so a failure
        # at e.g. f03 must not truncate the manifest before the later f48 that
        # actually built successfully.
        regions_out.append({"id": region["id"], "bounds": grid["bounds"], "cycle": cyc_iso, "hours": max_ok + 1})
    # carryover previous cycle files
    for p in sitemod.plan_carryover(prev, cid):
        dst = site_dir / p["path"]; dst.mkdir(parents=True, exist_ok=True)
        for fhr in range(p["hours"]):
            for name in (f"f{fhr:02d}.smk1", f"hpbl_f{fhr:02d}.bin"):
                try:
                    (dst / name).write_bytes(http(f"{args.pages_base.rstrip('/')}/{p['path']}/{name}", timeout=60))
                except Exception:
                    pass
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (site_dir / "manifest.json").write_text(sitemod.build_manifest(CFG["active"], regions_out, now_iso))
    write_observations(site_dir, regions_out)
    (site_dir / ".nojekyll").write_text("")
    print(f"built cycle {cid}: {sum(r['hours'] for r in regions_out)} tiles")

if __name__ == "__main__":
    main()
