# tests/test_plumes.py — seeding regions from the surface-smoke field itself.
#
# Plan-3 amendment (Erik, 2026-07-27): regions follow the SMOKE, not the fire
# list — a fire 800 miles away can put a town at AQI 200-300 while nothing
# burns nearby, and only the smoke field can see that.
import numpy as np
from pipeline.plumes import smoke_peaks, region_id_for

BOUNDS = {"west": -125.0, "south": 24.5, "east": -66.5, "north": 49.5}


def _field(ny=100, nx=160):
    return np.zeros((ny, nx))


def test_picks_peaks_above_threshold_only():
    f = _field(); f[60, 20] = 80.0; f[30, 100] = 5.0     # 5 µg/m³ is below the gate
    peaks = smoke_peaks(f, BOUNDS, threshold_ugm3=10.0, limit=6, min_separation_km=500.0)
    assert len(peaks) == 1 and peaks[0]["ugm3"] == 80.0


def test_min_separation_suppresses_same_plume_and_frees_the_slot():
    f = _field()
    f[60, 20] = 80.0; f[61, 22] = 70.0                    # same plume, ~2 cells apart
    f[30, 120] = 40.0                                     # distinct plume far east
    peaks = smoke_peaks(f, BOUNDS, threshold_ugm3=10.0, limit=2, min_separation_km=500.0)
    assert [round(p["ugm3"]) for p in peaks] == [80, 40]  # neighbour skipped, slot reused


def test_peak_lat_lon_is_the_cell_centre():
    f = _field(); f[0, 0] = 50.0
    p = smoke_peaks(f, BOUNDS, threshold_ugm3=10.0, limit=1, min_separation_km=0.0)[0]
    dlat = (BOUNDS["north"] - BOUNDS["south"]) / 100
    dlon = (BOUNDS["east"] - BOUNDS["west"]) / 160
    assert abs(p["lat"] - (24.5 + 0.5 * dlat)) < 1e-9
    assert abs(p["lon"] - (-125.0 + 0.5 * dlon)) < 1e-9


def test_region_id_snaps_to_one_degree():
    assert region_id_for(44.42, -120.71) == "plume-44n121w"


def test_undefined_fill_cells_are_not_peaks():
    """wgrib2 fills cells outside HRRR's native domain with 9.999e20; those
    seeded four phantom plume regions over the Atlantic/Pacific on the first
    live multi-region run (2026-07-28). UNDEFINED must never win a slot."""
    f = _field()
    f[0, 0] = 9.999e20          # ocean corner, outside the model domain
    f[60, 20] = 80.0            # real plume
    peaks = smoke_peaks(f, BOUNDS, threshold_ugm3=10.0, limit=6, min_separation_km=500.0)
    assert len(peaks) == 1 and peaks[0]["ugm3"] == 80.0
