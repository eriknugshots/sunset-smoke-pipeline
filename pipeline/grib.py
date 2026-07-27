# pipeline/grib.py — byte-range download, wgrib2 regrid to the region grid,
# and parsing of wgrib2 -bin output (f32 fields with Fortran record markers).
import struct, subprocess, urllib.request
import numpy as np


def region_grid_args(region):
    km_lat = 111.0
    km_lon = 111.0 * float(np.cos(np.radians(region["centerLat"])))
    half_lat = (region["widthKm"] / 2) / km_lat
    half_lon = (region["widthKm"] / 2) / km_lon
    south, west = region["centerLat"] - half_lat, region["centerLon"] - half_lon
    dlat = 2 * half_lat / region["ny"]
    dlon = 2 * half_lon / region["nx"]
    return {"lon": f"{west}:{region['nx']}:{dlon}", "lat": f"{south}:{region['ny']}:{dlat}",
            "bounds": {"west": west, "south": south,
                       "east": west + dlon * region["nx"], "north": south + dlat * region["ny"]}}


def fetch_ranges(url, ranges, out_path):
    """Ranged GETs appended into one multi-message GRIB file."""
    with open(out_path, "wb") as f:
        for start, end in ranges:
            hdr = f"bytes={start}-" if end is None else f"bytes={start}-{end - 1}"
            req = urllib.request.Request(url, headers={"Range": hdr})
            with urllib.request.urlopen(req, timeout=120) as r:
                f.write(r.read())


def regrid_to_bin(grib_path, grid, match_regex, bin_path):
    """wgrib2: select records by -match, regrid to regular latlon, dump -bin (native LE floats)."""
    cmd = ["wgrib2", str(grib_path), "-match", match_regex,
           "-new_grid_winds", "earth", "-new_grid", "latlon",
           grid["lon"], grid["lat"], "/dev/null", "-bin", str(bin_path)]
    # NOTE: wgrib2 option semantics need real-data verification (Task 8): the intent is
    # "regrid the matched records, discard the regridded grib output, dump the regridded
    # fields as -bin". If -bin captures the SOURCE grid instead of the regridded one on
    # the installed wgrib2 build, switch to the two-step form:
    #   wgrib2 in.grib2 -match RE -new_grid_winds earth -new_grid latlon LON LAT tmp.grib2
    #   wgrib2 tmp.grib2 -bin out.bin
    # Keep this comment until Task 8 confirms which form is correct.
    subprocess.run(cmd, check=True, capture_output=True)


def parse_bin(blob, nx, ny, nfields):
    out = np.empty((nfields, ny, nx), dtype=np.float32)
    o = 0
    for i in range(nfields):
        (n,) = struct.unpack_from("<I", blob, o); o += 4
        assert n == nx * ny * 4, f"field {i}: marker {n} != {nx*ny*4}"
        out[i] = np.frombuffer(blob, dtype="<f4", count=nx * ny, offset=o).reshape(ny, nx)
        o += n
        (n2,) = struct.unpack_from("<I", blob, o); o += 4
        assert n2 == n, f"field {i}: trailing marker {n2} != {n}"
    return out


def wgrib2_available():
    try:
        subprocess.run(["wgrib2", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False
