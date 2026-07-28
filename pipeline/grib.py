# pipeline/grib.py — byte-range download, wgrib2 regrid to the region grid,
# and parsing of wgrib2 -bin output (f32 fields with Fortran record markers).
import struct, subprocess, time, urllib.request
from pathlib import Path
import numpy as np


def region_grid_args(region):
    # Explicit-bounds form (the CONUS base region): not expressible as a square
    # km box around a centre, so the configured bounds are taken verbatim.
    if "bounds" in region:
        b = region["bounds"]
        dlat = (b["north"] - b["south"]) / region["ny"]
        dlon = (b["east"] - b["west"]) / region["nx"]
        return {"lon": f"{b['west']:.8f}:{region['nx']}:{dlon:.10f}",
                "lat": f"{b['south']:.8f}:{region['ny']}:{dlat:.10f}",
                "bounds": dict(b)}
    km_lat = 111.0
    km_lon = 111.0 * float(np.cos(np.radians(region["centerLat"])))
    half_lat = (region["widthKm"] / 2) / km_lat
    half_lon = (region["widthKm"] / 2) / km_lon
    south, west = region["centerLat"] - half_lat, region["centerLon"] - half_lon
    dlat = 2 * half_lat / region["ny"]
    dlon = 2 * half_lon / region["nx"]
    return {"lon": f"{west:.8f}:{region['nx']}:{dlon:.10f}",
            "lat": f"{south:.8f}:{region['ny']}:{dlat:.10f}",
            "bounds": {"west": west, "south": south,
                       "east": west + dlon * region["nx"], "north": south + dlat * region["ny"]}}


def fetch_ranges(url, ranges, out_path):
    """Ranged GETs appended into one multi-message GRIB file.

    Each range gets a bounded retry (2 attempts, 2s apart) for transient blips,
    and a hard check that the server actually honored the Range request — a
    server that ignores Range and returns 200 would otherwise silently append
    the full response body per record.
    """
    with open(out_path, "wb") as f:
        for start, end in ranges:
            hdr = f"bytes={start}-" if end is None else f"bytes={start}-{end - 1}"
            req = urllib.request.Request(url, headers={"Range": hdr})
            attempt = 0
            while True:
                attempt += 1
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        if r.status != 206:
                            raise RuntimeError(
                                f"expected 206 Partial Content, got {r.status} for {hdr} on {url}")
                        f.write(r.read())
                    break
                except Exception:
                    if attempt >= 2:
                        raise
                    time.sleep(2)


def regrid_to_bin(grib_path, grid, match_regex, bin_path):
    """wgrib2 two-step: (1) regrid matched records to the regular latlon region grid into a
    temp grib2, (2) dump that REGRIDDED file's fields as -bin (f32, Fortran record markers).

    Two-step is REQUIRED (settled by the first real CI run, 2026-07-27, wgrib2 3.8.0
    conda-forge): the one-step chain `-new_grid ... /dev/null -bin out` wrote a 0-byte bin,
    because -bin dumps records read from the INPUT file while -new_grid writes regridded
    records only to its own output file — chaining captures nothing. Record order in the
    regridded temp file is the input-file order of matched records (wgrib2 processes
    sequentially), i.e. exactly the order fetch_ranges wrote the ranges.
    """
    tmp = Path(str(bin_path) + ".regrid.grib2")
    steps = (
        ["wgrib2", str(grib_path), "-match", match_regex,
         "-new_grid_winds", "earth", "-new_grid", "latlon",
         grid["lon"], grid["lat"], str(tmp)],
        ["wgrib2", str(tmp), "-bin", str(bin_path)],
    )
    try:
        for cmd in steps:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"wgrib2 failed: {e.stderr.decode(errors='replace')[:500]}") from e
    finally:
        tmp.unlink(missing_ok=True)
    if Path(bin_path).stat().st_size == 0:
        raise RuntimeError(f"wgrib2 -bin produced an empty file for {match_regex} on {grib_path}")


def parse_bin(blob, nx, ny, nfields):
    out = np.empty((nfields, ny, nx), dtype=np.float32)
    o = 0
    for i in range(nfields):
        (n,) = struct.unpack_from("<I", blob, o); o += 4
        if n != nx * ny * 4:
            raise ValueError(f"field {i}: marker {n} != {nx*ny*4}")
        out[i] = np.frombuffer(blob, dtype="<f4", count=nx * ny, offset=o).reshape(ny, nx)
        o += n
        (n2,) = struct.unpack_from("<I", blob, o); o += 4
        if n2 != n:
            raise ValueError(f"field {i}: trailing marker {n2} != {n}")
    return out


def wgrib2_available():
    try:
        subprocess.run(["wgrib2", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False
