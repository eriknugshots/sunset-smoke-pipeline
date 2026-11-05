# sunset-smoke-pipeline

This repo builds SMK1 smoke-volume tiles from NOAA HRRR-Smoke (MASSDEN on hybrid levels) for the Sunset Prediction app. Tiles and a manifest are served from this repo's GitHub Pages. The tile format and full design spec live in the app repo at `docs/superpowers/specs/2026-07-26-smoke-layer-design.md`.

Builds are started by `tick.yml`, which checks NOAA every 10 minutes (`tick.py`) and starts `build-tiles.yml` once a new cycle is fully uploaded; GitHub's own cron fired only ~4 times a day and stays as a backstop. Each build: detect the latest synoptic HRRR cycle (00/06/12/18z), byte-range only the needed GRIB records via the `.idx` sidecars, regrid with wgrib2 to each region's lat/lon grid, resample with numpy to 40 uniform 250 m slabs, then log-encode the result into SMK1 tiles plus a `manifest.json`. The output is force-pushed as one orphan commit to `gh-pages`, carrying the previous cycle forward and keeping the latest two cycles available.

For local dev, set up a virtualenv with `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest`, and on macOS install wgrib2 with `brew install wgrib2`. Run a quick partial cycle with `python run_cycle.py --hours 2 --site-dir site`, and run the test suite with `.venv/bin/pytest`.
