# tests/test_tick.py — the tick's "start a build?" decision (tick.py), no network.
import datetime

import tick

UTC = datetime.timezone.utc
CYC = datetime.datetime(2026, 11, 4, 18, tzinfo=UTC)
CID = "20261104t18z"
FULL = 49


def _manifest(cid=CID, hours=FULL, n=3):
    return {"regions": [{"id": f"r{i}", "cycleId": cid, "hours": hours} for i in range(n)]}


def test_published_complete_on_newest_cycle_needs_nothing():
    assert tick.needs_build(_manifest(), CYC, FULL, final_ready=True, now=CYC) is False


def test_new_cycle_fully_uploaded_needs_a_build():
    m = _manifest(cid="20261104t12z")
    assert tick.needs_build(m, CYC, FULL, final_ready=True, now=CYC) is True


def test_new_cycle_still_uploading_waits_for_its_last_hour():
    # f01 is up but f48 is not: the previous cycle still covers 48 h, so wait
    # rather than publish a short cycle and rebuild it again 30 min later.
    m = _manifest(cid="20261104t12z")
    now = CYC + datetime.timedelta(hours=1, minutes=10)
    assert tick.needs_build(m, CYC, FULL, final_ready=False, now=now) is False


def test_last_hour_that_never_lands_builds_anyway_after_the_grace():
    m = _manifest(cid="20261104t12z")
    now = CYC + tick.FINAL_GRACE + datetime.timedelta(minutes=1)
    assert tick.needs_build(m, CYC, FULL, final_ready=False, now=now) is True


def test_published_short_on_this_cycle_tops_up_once_the_last_hour_lands():
    m = _manifest(hours=30)
    assert tick.needs_build(m, CYC, FULL, final_ready=True, now=CYC) is True
    assert tick.needs_build(m, CYC, FULL, final_ready=False, now=CYC + datetime.timedelta(hours=1)) is False


def test_one_region_short_counts():
    m = _manifest()
    m["regions"][1]["hours"] = 12
    assert tick.needs_build(m, CYC, FULL, final_ready=True, now=CYC) is True


def test_unreadable_or_empty_manifest_builds():
    assert tick.needs_build(None, CYC, FULL, final_ready=True, now=CYC) is True
    assert tick.needs_build({"regions": []}, CYC, FULL, final_ready=True, now=CYC) is True


def test_no_cycle_available_builds_nothing():
    assert tick.needs_build(_manifest(cid="x"), None, FULL, final_ready=False, now=CYC) is False
