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
    """Per-column np.interp from hybrid-level heights onto TERRAIN-FOLLOWING slabs.

    mass_kgm3, hgt_m: (nlev, ny, nx) arrays, hgt_m ascending along axis 0
    (hybrid levels are ordered surface-up by construction).

    Slab k of a column covers (k + 0.5) * z_step_m metres ABOVE THAT COLUMN'S
    GROUND, i.e. targets are `hgt_m[0, y, x] + (k + 0.5) * z_step_m`. Slab 0 is
    therefore the air a person standing there is breathing.

    This replaced an absolute-MSL grid on 2026-07-27 after a live diagnosis: with
    MSL slabs every slab below the column's lowest hybrid level was zeroed by
    np.interp's `left=0.0`, so at Prineville (ground 918 m) the first slab holding
    any data was 1125 m — 207 m overhead — and at Bend 233 m overhead. The entire
    near-surface layer was missing everywhere, so a viewer at ground level always
    sat underneath the data and the smoke could only ever be looked UP at. Slab
    centres are still uniform, so `zStepM` keeps its meaning; only the datum moves.
    """
    # Columns outside the source model's native domain: the CONUS latlon box's
    # ocean corners exceed HRRR's Lambert grid and wgrib2 fills them with
    # UNDEFINED (9.999e20). Observed live 2026-07-28: feeding those through the
    # ascending assert zeroed out the ENTIRE conus region and every plume box
    # touching open water. They become empty air over sea-level terrain instead.
    undef = ~np.all(np.isfinite(hgt_m) & (hgt_m < 1e19), axis=0)
    if np.all(undef):
        raise ValueError(
            "every column is undefined/out-of-domain — that is a wrong-records "
            "bug (or a region entirely outside the model grid), not an ocean corner")
    if not np.all(np.diff(hgt_m[:, ~undef], axis=0) > 0.0):
        raise ValueError(
            "hgt_m must be strictly ascending along axis 0 (hybrid levels surface-up); "
            "non-ascending or non-finite heights suggest wrong record ordering from the "
            ".idx / wgrib2 -bin output (see Task 8 -bin ambiguity)")
    nlev, ny, nx = mass_kgm3.shape
    agl = (np.arange(nz) + 0.5) * z_step_m
    out = np.zeros((nz, ny, nx), dtype=np.float64)
    for y in range(ny):
        for x in range(nx):
            if undef[y, x]:
                continue                       # out-of-domain -> stays all-zero
            col_h = hgt_m[:, y, x]
            col_m = mass_kgm3[:, y, x]
            # ground-relative targets; right=0.0 still zeroes above the column top,
            # while left is now unreachable (targets start above col_h[0]).
            v = np.interp(col_h[0] + agl, col_h, col_m, left=0.0, right=0.0)
            out[:, y, x] = v
    return out


def build_tile_arrays(mass_kgm3, hgt_m, nz, z_step_m):
    """Assemble the terrain (m MSL) and log-encoded density arrays for a tile.

    Terrain stays absolute (m MSL) — it is what the client anchors the volume to.
    Density is terrain-following: slab k is (k + 0.5) * z_step_m above terrain.
    """
    terr = hgt_m[0].astype(np.float32)                       # lowest hybrid level ~ ground
    terr[~(np.isfinite(terr) & (terr < 1e19))] = 0.0         # out-of-domain -> sea level
    dens = resample_columns(mass_kgm3, hgt_m, nz, z_step_m)
    dens_u8 = log_encode(dens.reshape(-1) * 1e9)             # (z, y, x) flat == (z*ny + y)*nx + x
    return terr, dens_u8
