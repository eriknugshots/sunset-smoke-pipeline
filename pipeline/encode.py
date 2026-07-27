# pipeline/encode.py — perceptual log curve + SMK1 writer.
# SMK1 (little-endian): 'SMK1' u32 | bounds WSEN 4xf32 | nx,ny,nz 3xu16 |
# zBottomM,zStepM 2xf32 | densScale f32 | terrain nx*ny f32 (m MSL) |
# zlib-deflate u8 density, index (z*ny + y)*nx + x, y0 = SOUTH row.
# Must stay byte-compatible with the app's JS codec (src/smoke.js).
# Density semantics: app spec §B (authoritative). Curve anchors: 0.2 -> 0, 300 -> 255 µg/m³.
import struct, zlib
import numpy as np

C_LO, C_HI = 0.2, 300.0


def log_encode(c_ugm3):
    c = np.asarray(c_ugm3, dtype=np.float64)
    finite = np.isfinite(c)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (np.log10(np.maximum(c, 1e-12)) - np.log10(C_LO)) / (np.log10(C_HI) - np.log10(C_LO))
    b = np.clip(np.round(t * 255.0), 0, 255)
    b[c < C_LO] = 0
    b[~finite] = 0
    return b.astype(np.uint8)


def encode_smk1(bounds, nx, ny, nz, z_bottom_m, z_step_m, dens_scale, terrain_f32, density_u8):
    terrain_f32 = np.asarray(terrain_f32, dtype="<f4")
    density_u8 = np.asarray(density_u8, dtype=np.uint8)
    if terrain_f32.size != nx * ny:
        raise ValueError(f"terrain size {terrain_f32.size} != {nx * ny}")
    if density_u8.size != nx * ny * nz:
        raise ValueError(f"density size {density_u8.size} != {nx * ny * nz}")
    head = struct.pack("<I4f3H3f", 0x314B4D53,
                       bounds["west"], bounds["south"], bounds["east"], bounds["north"],
                       nx, ny, nz, z_bottom_m, z_step_m, dens_scale)
    if len(head) != 38:
        raise ValueError(f"header is {len(head)} bytes, expected 38 (padding leak?)")
    terr = terrain_f32.tobytes()
    dens = zlib.compress(density_u8.tobytes(), 6)
    return head + terr + dens
