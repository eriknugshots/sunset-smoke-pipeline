import numpy as np
from pipeline.tile import resample_columns, build_tile_arrays


def test_resample_puts_mass_at_right_slab():
    # 1 column, 3 hybrid levels at 100/1000/3000 m with MASSDEN 5/1/0 µg/m³ (as kg/m³)
    hgt = np.array([[[100.0]], [[1000.0]], [[3000.0]]])
    mass = np.array([[[5e-9]], [[1e-9]], [[0.0]]])
    dens = resample_columns(mass, hgt, nz=8, z_step_m=250.0)   # slabs 0..2000 m, centers 125..1875
    assert dens.shape == (8, 1, 1)
    ug = dens * 1e9
    assert ug[0, 0, 0] > 4.0              # 125 m: near the 5 µg surface value
    assert 0.5 < ug[3, 0, 0] < 5.0        # 875 m: between levels
    assert ug[7, 0, 0] < 1.0              # 1875 m: approaching the 1 µg level then falling


def test_resample_zero_above_top_and_below_bottom():
    hgt = np.array([[[500.0]], [[800.0]]])
    mass = np.array([[[3e-9]], [[3e-9]]])
    dens = resample_columns(mass, hgt, nz=8, z_step_m=250.0)
    assert dens[0, 0, 0] == 0.0           # 125 m: below the lowest level -> 0
    assert dens[7, 0, 0] == 0.0           # 1875 m: above the top level -> 0


def test_build_tile_arrays_shapes_and_terrain():
    nz_src, ny, nx = 3, 2, 2
    hgt = np.linspace(100, 3000, nz_src * ny * nx).reshape(nz_src, ny, nx)
    mass = np.full((nz_src, ny, nx), 2e-9)
    terr, dens_u8 = build_tile_arrays(mass, hgt, nz=4, z_step_m=250.0)
    assert terr.shape == (ny, nx) and terr.dtype == np.float32
    assert np.array_equal(terr, hgt[0].astype(np.float32))    # terrain = lowest hybrid HGT
    assert dens_u8.shape == (4 * ny * nx,) and dens_u8.dtype == np.uint8
