# pipeline/observations.py — EPA AirNow ground-truth PM2.5 observations.
#
# HRRR-Smoke over-forecasts surface PM2.5 (measured 3.4x high at Prineville OR,
# 2026-07-27: HRRR 57 ug/m3 vs the nearest EPA monitor's 16.7 ug/m3). Publishing
# real AirNow observations alongside the tiles lets the client bias-correct both
# the AQI number and the rendered volume.
#
# No API key required. Two public files on files.airnowtech.org:
#
#  1. Hourly observations, HourlyData_YYYYMMDDHH.dat (timestamp is UTC). The
#     current hour usually 404s (not published yet) -- fetch_observations()
#     walks back hour by hour until one returns 200. Pipe-delimited, no header:
#       MM/DD/YY|HH:MM|AQSID|SiteName|GMTOffset|Parameter|Units|Value|AgencyName
#
#  2. Site locations, Monitoring_Site_Locations_V2.dat (~4.3 MB, pipe-delimited,
#     HAS a header row). Field indices: 1=AQSID, 3=Parameter, 6=SiteName,
#     7=Status, 11=Latitude, 12=Longitude. Some rows have empty lat/lon.
#
# Join: hourly row field[2] (AQSID) -> site dict keyed by AQSID.
import datetime
import math
import urllib.error
import urllib.request

SITES_URL = "https://files.airnowtech.org/airnow/today/Monitoring_Site_Locations_V2.dat"
HOURLY_URL_TMPL = "https://files.airnowtech.org/airnow/today/HourlyData_{}.dat"


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def parse_sites(text):
    """Monitoring_Site_Locations_V2.dat text -> {aqsid: {"name","lat","lon"}}.

    Keeps only PM2.5 rows with finite coordinates. The first line is a header
    row (starts with "StationID|...") and is always skipped; any other
    malformed row (too few fields, non-numeric lat/lon, blank lat/lon) is
    silently dropped rather than raising, since this is a large third-party
    feed we don't control the exact shape of every row of.
    """
    out = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if i == 0 or not line.strip():
            continue
        fields = line.split("|")
        if len(fields) < 13:
            continue
        if fields[3] != "PM2.5":
            continue
        aqsid = fields[1].strip()
        if not aqsid:
            continue
        try:
            lat = float(fields[11])
            lon = float(fields[12])
        except ValueError:
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        out[aqsid] = {"name": fields[6].strip(), "lat": lat, "lon": lon}
    return out


def parse_hourly(text, sites):
    """HourlyData_YYYYMMDDHH.dat text + parse_sites() output ->
    [{"aqsid","name","lat","lon","ugm3","time"}].

    Keeps only PM2.5 rows, drops negative values (instrument noise) and rows
    whose AQSID has no matching site. `time` is built from the file's own
    MM/DD/YY|HH:MM fields (which are UTC) as "20YY-MM-DDTHH:MM:00Z".
    """
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) < 9:
            continue
        if fields[5] != "PM2.5":
            continue
        aqsid = fields[2].strip()
        site = sites.get(aqsid)
        if site is None:
            continue
        try:
            ugm3 = float(fields[7])
        except ValueError:
            continue
        if ugm3 < 0:
            continue
        try:
            mm, dd, yy = fields[0].split("/")
            hh, minute = fields[1].split(":")
        except ValueError:
            continue
        time_iso = f"20{yy}-{mm}-{dd}T{hh}:{minute}:00Z"
        out.append({
            "aqsid": aqsid, "name": site["name"], "lat": site["lat"], "lon": site["lon"],
            "ugm3": ugm3, "time": time_iso,
        })
    return out


def fetch_observations(now=None, opener=None, max_lookback=4):
    """Fetch the sites file and the newest available hourly file, walking back
    hour by hour (up to max_lookback attempts) from `now` (UTC, defaults to
    datetime.now) until one returns 200 -- the current hour usually 404s
    because AirNow hasn't published it yet.

    `opener(url, timeout) -> bytes` is injectable for tests; the default hits
    the live files.airnowtech.org endpoints and raises urllib.error.HTTPError
    naturally on a 404, which is what the walk-back catches.

    Returns (observations, iso_hour_used). Raises RuntimeError if every
    attempt in the lookback window 404s (or otherwise fails).
    """
    opener = opener or _get
    now = (now or datetime.datetime.now(datetime.timezone.utc))
    now = now.replace(minute=0, second=0, microsecond=0)

    sites = parse_sites(opener(SITES_URL, 60).decode("utf-8", errors="replace"))

    last_err = None
    for back_h in range(max_lookback):
        t = now - datetime.timedelta(hours=back_h)
        url = HOURLY_URL_TMPL.format(t.strftime("%Y%m%d%H"))
        try:
            raw = opener(url, 60)
        except urllib.error.HTTPError as e:
            last_err = e
            continue
        text = raw.decode("utf-8", errors="replace")
        observations = parse_hourly(text, sites)
        iso_hour = t.strftime("%Y-%m-%dT%H:00:00Z")
        return observations, iso_hour

    raise RuntimeError(
        f"no AirNow hourly file available in the last {max_lookback} hour(s): {last_err}"
    )


def observations_in_bounds(observations, bounds):
    """Filter observations to those within `bounds` (west/south/east/north,
    the same shape grib.region_grid_args() returns)."""
    return [
        o for o in observations
        if bounds["west"] <= o["lon"] <= bounds["east"]
        and bounds["south"] <= o["lat"] <= bounds["north"]
    ]
