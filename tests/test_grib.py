import numpy as np

from pipeline.grib import region_grid_args, parse_bin


def test_region_grid_args_matches_regions_json():
    args = region_grid_args({"centerLat": 44.06, "centerLon": -121.32, "widthKm": 480, "nx": 160, "ny": 160})
    lon0, nxs, dlon = args["lon"].split(":")
    lat0, nys, dlat = args["lat"].split(":")
    assert nxs == "160" and nys == "160"
    south = 44.06 - 240 / 111.0
    assert abs(float(lat0) - south) < 1e-6 and float(dlat) > 0        # row 0 = south (matches SMK1)
    assert abs(float(lat0) + float(dlat) * 160 - (44.06 + 240 / 111.0)) < 1e-6   # north edge closes the box
    b = args["bounds"]
    assert b["south"] < 44.06 < b["north"] and b["west"] < -121.32 < b["east"]


def test_parse_bin_roundtrip():
    import struct
    fields = [np.arange(12, dtype="<f4").reshape(3, 4), (np.ones((3, 4), dtype="<f4") * 7)]
    blob = b""
    for f in fields:
        raw = f.tobytes()
        blob += struct.pack("<I", len(raw)) + raw + struct.pack("<I", len(raw))
    out = parse_bin(blob, 4, 3, 2)
    assert out.shape == (2, 3, 4) and out[1, 0, 0] == 7.0


def test_parse_bin_rejects_wrong_marker():
    import struct
    raw = np.zeros(12, dtype="<f4").tobytes()
    blob = struct.pack("<I", 99) + raw            # wrong Fortran marker
    import pytest
    with pytest.raises(ValueError):
        parse_bin(blob, 4, 3, 1)


def test_region_grid_args_explicit_bounds():
    """The CONUS base region is not a square km box around a centre, so it is
    configured with explicit degree bounds taken verbatim."""
    r = {"bounds": {"west": -125.0, "south": 24.5, "east": -66.5, "north": 49.5},
         "nx": 160, "ny": 100}
    g = region_grid_args(r)
    assert g["bounds"] == r["bounds"]
    # wgrib2 args carry west/south + counts + steps (dlon = 58.5/160, dlat = 25/100)
    assert g["lon"].startswith("-125.0") and ":160:" in g["lon"]
    assert g["lat"].startswith("24.5") and ":100:" in g["lat"]
