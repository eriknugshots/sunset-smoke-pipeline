# tests/test_observations.py — EPA AirNow ground-truth parsing/fetch contracts.
# All fixtures are small inline strings; no network access happens in these tests.
import datetime
import urllib.error

import pytest

from pipeline.observations import (
    parse_sites, parse_hourly, fetch_observations, observations_in_bounds,
)

# Sites fixture: header row (skipped) + PM2.5 row + non-PM2.5 row (OZONE, skipped)
# + a PM2.5 row with empty lat/lon (skipped). Field indices per spec:
# 1=AQSID, 3=Parameter, 6=SiteName, 7=Status, 11=Latitude, 12=Longitude.
SITES_TEXT = (
    "StationID|AQSID|FullAQSID|Parameter|Something|Something|SiteName|Status|X|Y|Z|Latitude|Longitude\n"
    "1|000010601|840000010601|PM2.5|a|b|Goose Bay|Active|x|y|z|53.30|-60.41\n"
    "2|410170601|840410170601|PM2.5|a|b|Prineville - Davidson Park|Active|x|y|z|44.2996|-120.8259\n"
    "3|999999999|840999999999|OZONE|a|b|Somewhere Ozone Site|Active|x|y|z|10.0|10.0\n"
    "4|888888888|840888888888|PM2.5|a|b|No Coords Site|Active|x|y|z||\n"
)

# Hourly fixture: PM2.5 row for a known site, negative-value row (dropped),
# non-PM2.5 row (dropped), and a PM2.5 row for an AQSID with no matching site (dropped).
HOURLY_TEXT = (
    "07/27/26|19:00|410170601|Prineville - Davidson Park|-8|PM2.5|UG/M3|16.7|USDA Forest Service\n"
    "07/27/26|19:00|000010601|Goose Bay|-4|PM2.5|UG/M3|-3.2|Canadian Air and Precipitation Monitoring Network\n"
    "07/27/26|19:00|000010601|Goose Bay|-4|OZONE|PPB|38|Canadian Air and Precipitation Monitoring Network\n"
    "07/27/26|19:00|000000000|Unknown Site|-4|PM2.5|UG/M3|12.0|Some Agency\n"
)


def test_parse_sites_skips_header_and_keeps_pm25_with_coords():
    sites = parse_sites(SITES_TEXT)
    assert set(sites) == {"000010601", "410170601"}
    assert sites["410170601"]["name"] == "Prineville - Davidson Park"
    assert sites["410170601"]["lat"] == pytest.approx(44.2996)
    assert sites["410170601"]["lon"] == pytest.approx(-120.8259)


def test_parse_sites_skips_non_pm25_row():
    sites = parse_sites(SITES_TEXT)
    assert "999999999" not in sites


def test_parse_sites_skips_empty_lat_lon_row():
    sites = parse_sites(SITES_TEXT)
    assert "888888888" not in sites


def test_parse_hourly_drops_negatives_non_pm25_and_unmatched():
    sites = parse_sites(SITES_TEXT)
    obs = parse_hourly(HOURLY_TEXT, sites)
    # Only the Prineville row survives: Goose Bay PM2.5 is negative, Goose Bay
    # OZONE isn't PM2.5, and AQSID 000000000 has no matching site.
    assert len(obs) == 1
    assert obs[0]["aqsid"] == "410170601"
    assert obs[0]["name"] == "Prineville - Davidson Park"
    assert obs[0]["ugm3"] == pytest.approx(16.7)


def test_parse_hourly_formats_time_exactly():
    sites = parse_sites(SITES_TEXT)
    obs = parse_hourly(HOURLY_TEXT, sites)
    assert obs[0]["time"] == "2026-07-27T19:00:00Z"


def test_observations_in_bounds_includes_inside_excludes_outside():
    observations = [
        {"name": "Inside", "lat": 44.30, "lon": -120.83, "ugm3": 16.7},
        {"name": "Outside", "lat": 10.0, "lon": 10.0, "ugm3": 5.0},
    ]
    bounds = {"west": -122.0, "south": 43.0, "east": -119.0, "north": 45.0}
    kept = observations_in_bounds(observations, bounds)
    assert [o["name"] for o in kept] == ["Inside"]


def test_fetch_observations_walks_back_past_404s():
    now = datetime.datetime(2026, 7, 27, 19, 30, tzinfo=datetime.timezone.utc)
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if "Monitoring_Site_Locations" in url:
            return SITES_TEXT.encode()
        # Current hour (19z) and previous hour (18z) 404; 17z succeeds.
        if "HourlyData_2026072719" in url or "HourlyData_2026072718" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "HourlyData_2026072717" in url:
            return HOURLY_TEXT.encode()
        raise AssertionError(f"unexpected url {url}")

    # min_observations=1: this test covers walking past 404s, not the completeness
    # threshold, and its fixture deliberately holds a single row.
    observations, iso_hour = fetch_observations(now=now, opener=opener, max_lookback=4,
                                                min_observations=1)
    assert iso_hour == "2026-07-27T17:00:00Z"
    assert len(observations) == 1
    assert observations[0]["name"] == "Prineville - Davidson Park"
    # sanity: it actually tried 19z and 18z before landing on 17z
    assert any("2026072719" in u for u in calls)
    assert any("2026072718" in u for u in calls)
    assert any("2026072717" in u for u in calls)


def test_fetch_observations_raises_when_all_attempts_404():
    now = datetime.datetime(2026, 7, 27, 19, 30, tzinfo=datetime.timezone.utc)

    def opener(url, timeout):
        if "Monitoring_Site_Locations" in url:
            return SITES_TEXT.encode()
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(RuntimeError):
        fetch_observations(now=now, opener=opener, max_lookback=4)


def test_fetch_skips_a_published_but_still_filling_hour():
    """AirNow publishes an hour early and monitors report into it for ~an hour,
    so the NEWEST file is typically a near-empty stub. Existing is not the same
    as being complete: taking the stub shipped a single far-away monitor and
    silently disabled the bias correction (observed 2026-07-27, 21:00 file had
    13 rows while 20:00 had 1256)."""
    sites = "\n".join(
        ["StationID|AQSID|FullAQSID|Parameter|MonitorType|SiteCode|SiteName|Status|"
         "AgencyID|AgencyName|EPARegion|Latitude|Longitude"]
        + [f"1|A{i}|F|PM2.5|Permanent|C|Site{i}|Active|AG|Agency|R10|44.{i:03d}|-121.0"
           for i in range(300)])

    def rows(n, hh):
        return "\n".join(f"07/27/26|{hh}:00|A{i}|Site{i}|-8|PM2.5|UG/M3|5.0|Agency"
                         for i in range(n))

    served = {}

    def opener(url, timeout):
        if "Monitoring_Site_Locations" in url:
            return sites.encode()
        stamp = url.rsplit("_", 1)[1].split(".")[0]
        served[stamp] = served.get(stamp, 0) + 1
        hh = stamp[-2:]
        if hh == "21":
            return rows(13, hh).encode()       # published but still filling
        if hh == "20":
            return rows(250, hh).encode()      # complete
        raise AssertionError(f"walked back too far: {stamp}")

    now = datetime.datetime(2026, 7, 27, 21, 30, tzinfo=datetime.timezone.utc)
    obs, hour = fetch_observations(now=now, opener=opener, min_observations=200)
    assert len(obs) == 250
    assert hour == "2026-07-27T20:00:00Z"


def test_fetch_falls_back_to_the_fullest_hour_when_none_reach_the_threshold():
    sites = ("StationID|AQSID|FullAQSID|Parameter|MonitorType|SiteCode|SiteName|Status|"
             "AgencyID|AgencyName|EPARegion|Latitude|Longitude\n"
             "1|A0|F|PM2.5|Permanent|C|Site0|Active|AG|Agency|R10|44.0|-121.0\n"
             "1|A1|F|PM2.5|Permanent|C|Site1|Active|AG|Agency|R10|44.1|-121.1")

    def opener(url, timeout):
        if "Monitoring_Site_Locations" in url:
            return sites.encode()
        hh = url.rsplit("_", 1)[1].split(".")[0][-2:]
        n = 2 if hh == "19" else 1             # 19:00 is the fullest available
        return "\n".join(f"07/27/26|{hh}:00|A{i}|Site{i}|-8|PM2.5|UG/M3|5.0|Agency"
                         for i in range(n)).encode()

    now = datetime.datetime(2026, 7, 27, 21, 30, tzinfo=datetime.timezone.utc)
    obs, hour = fetch_observations(now=now, opener=opener, max_lookback=3, min_observations=200)
    assert len(obs) == 2 and hour == "2026-07-27T19:00:00Z"
