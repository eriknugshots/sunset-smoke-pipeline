import numpy as np
import pytest
from pipeline.tile import resample_columns, build_tile_arrays


def test_resample_puts_mass_at_right_slab():
    # 1 column, 3 hybrid levels at 100/1000/3000 m with MASSDEN 5/1/0 µg/m³ (as kg/m³).
    # Slabs are ground-relative, so centres sit at 100 + 125, 100 + 375, ...
    hgt = np.array([[[100.0]], [[1000.0]], [[3000.0]]])
    mass = np.array([[[5e-9]], [[1e-9]], [[0.0]]])
    dens = resample_columns(mass, hgt, nz=8, z_step_m=250.0)
    assert dens.shape == (8, 1, 1)
    ug = dens * 1e9
    assert ug[0, 0, 0] > 4.0              # 225 m MSL: near the 5 µg surface value
    assert 0.5 < ug[3, 0, 0] < 5.0        # 975 m MSL: between levels
    assert ug[7, 0, 0] < 1.0              # 1975 m MSL: past the 1 µg level, falling


def test_resample_slab0_holds_the_surface_layer():
    """Slab 0 is the air at ground level, NOT zero.

    Regression guard for the 2026-07-27 diagnosis: with the old absolute-MSL
    grid this column's slab 0 (125 m MSL) fell below the 500 m ground and was
    zeroed, hiding the whole near-surface layer from a viewer standing in it.
    """
    hgt = np.array([[[500.0]], [[800.0]]])
    mass = np.array([[[3e-9]], [[3e-9]]])
    dens = resample_columns(mass, hgt, nz=8, z_step_m=250.0)
    assert dens[0, 0, 0] == pytest.approx(3e-9)   # 625 m MSL = 125 m AGL -> real smoke
    assert dens[7, 0, 0] == 0.0                   # 2375 m MSL: above the column top -> 0


def test_resample_is_ground_relative_not_msl():
    """Two columns with identical smoke-above-ground but different ground heights
    must produce identical slabs — that is what 'terrain-following' means."""
    low = resample_columns(np.array([[[4e-9]], [[1e-9]]]),
                           np.array([[[0.0]], [[2000.0]]]), nz=6, z_step_m=250.0)
    high = resample_columns(np.array([[[4e-9]], [[1e-9]]]),
                            np.array([[[1500.0]], [[3500.0]]]), nz=6, z_step_m=250.0)
    assert np.allclose(low, high)


def test_resample_raises_on_non_ascending_hgt():
    hgt = np.array([[[1000.0]], [[500.0]]])   # descending -> would silently interp to 0
    mass = np.array([[[3e-9]], [[3e-9]]])
    with pytest.raises(ValueError):
        resample_columns(mass, hgt, nz=8, z_step_m=250.0)


def test_resample_raises_on_nan_hgt():
    hgt = np.array([[[100.0]], [[np.nan]], [[3000.0]]])
    mass = np.array([[[5e-9]], [[1e-9]], [[0.0]]])
    with pytest.raises(ValueError):
        resample_columns(mass, hgt, nz=8, z_step_m=250.0)


def test_build_tile_arrays_shapes_and_terrain():
    nz_src, ny, nx = 3, 2, 2
    hgt = np.linspace(100, 3000, nz_src * ny * nx).reshape(nz_src, ny, nx)
    mass = np.full((nz_src, ny, nx), 2e-9)
    terr, dens_u8 = build_tile_arrays(mass, hgt, nz=4, z_step_m=250.0)
    assert terr.shape == (ny, nx) and terr.dtype == np.float32
    assert np.array_equal(terr, hgt[0].astype(np.float32))    # terrain = lowest hybrid HGT
    assert dens_u8.shape == (4 * ny * nx,) and dens_u8.dtype == np.uint8


def test_out_of_domain_columns_become_empty_not_fatal():
    """The CONUS latlon box's ocean corners fall outside HRRR's native Lambert
    grid; wgrib2 fills them with UNDEFINED (9.999e20). Observed live 2026-07-28:
    those columns made resample_columns raise (heights not ascending), zeroing
    out the ENTIRE conus region and every plume box touching open water, and
    the same fill seeded four phantom plume regions over the Atlantic/Pacific.
    Out-of-domain columns must decode as empty air over sea-level terrain."""
    nlev, ny, nx = 3, 1, 2
    hgt = np.zeros((nlev, ny, nx))
    mass = np.full((nlev, ny, nx), 5e-9)
    hgt[:, 0, 0] = [100.0, 600.0, 1100.0]
    hgt[:, 0, 1] = 9.999e20
    terr, dens = build_tile_arrays(mass, hgt, nz=4, z_step_m=250)
    d = dens.reshape(4, 1, 2)
    assert d[:, 0, 1].max() == 0 and terr[0, 1] == 0.0
    assert d[:, 0, 0].max() > 0 and terr[0, 0] == 100.0


def test_all_columns_out_of_domain_raises():
    """A fully-undefined field is a wrong-records bug, not an ocean corner —
    it must still fail loudly rather than publish an all-empty tile."""
    hgt = np.full((3, 1, 2), 9.999e20)
    mass = np.zeros((3, 1, 2))
    with pytest.raises(ValueError):
        build_tile_arrays(mass, hgt, nz=4, z_step_m=250)
