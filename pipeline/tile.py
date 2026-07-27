# pipeline/tile.py — vertical resample (terrain-following hybrid levels ->
# uniform 250 m slabs, spec §A.3) and tile array assembly.
#
# Flattening/orientation note: dens.reshape(-1) on a (nz, ny, nx) C-order array
# yields exactly (z*ny + y)*nx + x — matches the SMK1 format and the client
# shader. The wgrib2 latlon grid gives row 0 = south (dlat > 0), matching
# SMK1's y0 = south, so no row flip is needed here.
import numpy as np
from pipeline.encode import log_encode


def resample_columns(mass_kgm3, hgt_m, nz, z_step_m):
    """Per-column np.interp from hybrid-level heights onto uniform slab centers.

    mass_kgm3, hgt_m: (nlev, ny, nx) arrays, hgt_m ascending along axis 0
    (hybrid levels are ordered surface-up by construction). Slab centers are
    (i + 0.5) * z_step_m for i in [0, nz), matched against hgt_m in absolute
    meters (MSL) per column — same convention as the rest of the SMK1
    contract. Columns are zero outside the source level span (np.interp's
    left/right bounds are passed as 0.0 directly).
    """
    if not np.all(np.diff(hgt_m, axis=0) > 0.0):
        raise ValueError(
            "hgt_m must be strictly ascending along axis 0 (hybrid levels surface-up); "
            "non-ascending or non-finite heights suggest wrong record ordering from the "
            ".idx / wgrib2 -bin output (see Task 8 -bin ambiguity)")
    nlev, ny, nx = mass_kgm3.shape
    targets = (np.arange(nz) + 0.5) * z_step_m
    out = np.zeros((nz, ny, nx), dtype=np.float64)
    for y in range(ny):
        for x in range(nx):
            col_h = hgt_m[:, y, x]
            col_m = mass_kgm3[:, y, x]
            v = np.interp(targets, col_h, col_m, left=0.0, right=0.0)
            out[:, y, x] = v
    return out


def build_tile_arrays(mass_kgm3, hgt_m, nz, z_step_m):
    """Assemble the terrain (m MSL) and log-encoded density arrays for a tile."""
    terr = hgt_m[0].astype(np.float32)                       # lowest hybrid level ~ ground
    dens = resample_columns(mass_kgm3, hgt_m, nz, z_step_m)
    dens_u8 = log_encode(dens.reshape(-1) * 1e9)             # (z, y, x) flat == (z*ny + y)*nx + x
    return terr, dens_u8
