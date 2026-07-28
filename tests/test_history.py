from pipeline.history import hour_id, plan_history, history_path


def _prev(cycle="2026-07-27T18:00:00Z", cid="20260727t18z", hours=49, history=None):
    r = {"id": "home", "cycle": cycle, "cycleId": cid, "hours": hours,
         "path": f"tiles/home/{cid}", "historyPath": history_path("home")}
    if history is not None:
        r["history"] = history
    return {"regions": [r]}


def test_hour_id_is_hour_resolution():
    assert hour_id("2026-07-27T12:00:00Z") == "20260727t12z"
    assert hour_id("2026-07-28T00:00:00Z") == "20260728t00z"


def test_covers_only_hours_before_the_cycle():
    # cycle 00z, now 04z: 00z..04z live in the cycle dir, so the archive must
    # stop at the cycle and not duplicate them.
    plans = plan_history(_prev(), "home", "2026-07-28T00:00:00Z", "2026-07-28T04:00:00Z")
    times = [p["t"] for p in plans]
    assert times, "expected the previous cycle to cover the pre-cycle hours"
    assert max(times) == "2026-07-27T23:00:00Z"
    assert all(t < "2026-07-28T00:00:00Z" for t in times)


def test_pulls_pre_cycle_hours_from_the_previous_cycle():
    # 18z cycle covers 18z onward; hours 18z..23z become f00..f05.
    plans = plan_history(_prev(), "home", "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    by_t = {p["t"]: p for p in plans}
    assert by_t["2026-07-27T18:00:00Z"]["src"] == "tiles/home/20260727t18z/f00.smk1"
    assert by_t["2026-07-27T23:00:00Z"]["src"] == "tiles/home/20260727t18z/f05.smk1"
    assert by_t["2026-07-27T23:00:00Z"]["dst"] == "20260727t23z.smk1"


def test_existing_archive_is_carried_forward():
    old = "2026-07-27T06:00:00Z"          # older than the previous cycle (18z)
    plans = plan_history(_prev(history=[old]), "home",
                         "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    by_t = {p["t"]: p for p in plans}
    assert by_t[old]["src"] == "tiles/home/history/20260727t06z.smk1"


def test_archived_hour_wins_over_a_longer_lead_forecast():
    # 20z is reachable BOTH ways: f02 of the 18z cycle, or an existing archive
    # entry (which was itself written from a shorter lead). Prefer the archive.
    t = "2026-07-27T20:00:00Z"
    plans = plan_history(_prev(history=[t]), "home",
                         "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    src = next(p["src"] for p in plans if p["t"] == t)
    assert src == "tiles/home/history/20260727t20z.smk1"


def test_window_drops_anything_older_than_48h():
    old = "2026-07-25T00:00:00Z"          # ~48 h before now
    plans = plan_history(_prev(history=[old]), "home",
                         "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    assert all(p["t"] >= "2026-07-26T00:00:00Z" for p in plans)
    assert old not in [p["t"] for p in plans]


def test_window_is_configurable():
    plans = plan_history(_prev(), "home", "2026-07-28T00:00:00Z",
                         "2026-07-28T00:00:00Z", window_h=3)
    assert [p["t"] for p in plans] == ["2026-07-27T21:00:00Z",
                                       "2026-07-27T22:00:00Z",
                                       "2026-07-27T23:00:00Z"]


def test_hours_beyond_the_previous_cycles_extent_are_skipped():
    # A previous cycle that only got 3 hours out cannot supply f05.
    plans = plan_history(_prev(hours=3), "home",
                         "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    assert [p["t"] for p in plans] == ["2026-07-27T18:00:00Z",
                                       "2026-07-27T19:00:00Z",
                                       "2026-07-27T20:00:00Z"]


def test_first_ever_run_has_nothing_to_archive():
    assert plan_history(None, "home", "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z") == []


def test_unknown_region_is_not_sourced_from_another_regions_tiles():
    plans = plan_history(_prev(), "plume-45n120w",
                         "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    assert plans == []


def test_missing_historyPath_falls_back_to_the_conventional_location():
    old = "2026-07-27T06:00:00Z"
    prev = _prev(history=[old])
    del prev["regions"][0]["historyPath"]
    plans = plan_history(prev, "home", "2026-07-28T00:00:00Z", "2026-07-28T00:00:00Z")
    src = next(p["src"] for p in plans if p["t"] == old)
    assert src == "tiles/home/history/20260727t06z.smk1"


def test_write_archive_lands_files_and_records_only_what_landed(tmp_path):
    from pipeline.history import write_archive
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("f03.smk1"):
            raise OSError("404")          # one hour is missing upstream
        return b"TILE:" + url[-12:].encode()

    regions = [{"id": "home"}]
    write_archive(tmp_path, regions, _prev(), "2026-07-28T00:00:00Z",
                  "2026-07-28T00:00:00Z", "https://x.io/site/", fetch, log=lambda m: None)

    hist = tmp_path / "tiles" / "home" / "history"
    # 18z..23z minus the failed 21z (f03)
    assert sorted(p.name for p in hist.iterdir()) == [
        "20260727t18z.smk1", "20260727t19z.smk1", "20260727t20z.smk1",
        "20260727t22z.smk1", "20260727t23z.smk1"]
    assert "2026-07-27T21:00:00Z" not in regions[0]["history"], "must not advertise a missing hour"
    assert len(regions[0]["history"]) == 5
    assert (hist / "20260727t18z.smk1").read_bytes().startswith(b"TILE:")
    assert all(u.startswith("https://x.io/site/tiles/home/") for u in fetched)


def test_write_archive_is_a_noop_without_a_pages_base(tmp_path):
    from pipeline.history import write_archive
    regions = [{"id": "home"}]
    write_archive(tmp_path, regions, _prev(), "2026-07-28T00:00:00Z",
                  "2026-07-28T00:00:00Z", None, lambda u: b"x")
    assert "history" not in regions[0]
    assert not (tmp_path / "tiles").exists()
