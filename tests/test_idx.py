from pathlib import Path

import pytest

from pipeline.idx import parse_idx, select_records, byte_ranges

FIX = Path(__file__).parent / "fixtures"


def test_parse_idx_fields():
    recs = parse_idx((FIX / "wrfnat.idx").read_text())
    assert len(recs) > 1000
    r = recs[0]
    assert set(r) == {"n", "offset", "var", "level"} and r["offset"] == 0


def test_select_massden_hybrid_30():
    recs = parse_idx((FIX / "wrfnat.idx").read_text())
    sel = select_records(recs, "MASSDEN", "hybrid level", 30)
    assert len(sel) == 30
    assert [r["level"] for r in sel[:3]] == ["1 hybrid level", "2 hybrid level", "3 hybrid level"]


def test_select_hpbl_surface():
    recs = parse_idx((FIX / "wrfsfc.idx").read_text())
    assert len(select_records(recs, "HPBL", "surface", 1)) == 1


def test_byte_ranges_end_exclusive_and_last_open():
    recs = parse_idx((FIX / "wrfnat.idx").read_text())
    sel = select_records(recs, "MASSDEN", "hybrid level", 2)
    rngs = byte_ranges(sel, recs)
    assert rngs[0][1] > rngs[0][0]          # (start, end) with end = next record offset
    last_rec = max(recs, key=lambda r: r["offset"])
    assert byte_ranges([last_rec], recs)[0][1] is None


def test_select_massden_hybrid_excludes_8m_above_ground():
    # Truth check: "MASSDEN:8 m above ground" is present in BOTH fixtures
    # (wrfnat.idx line 1046, wrfsfc.idx line 76). wrfnat.idx is the one that
    # also carries the 50 real hybrid-level MASSDEN records, so it's the
    # meaningful fixture for proving the near-surface record gets excluded
    # from among them.
    text = (FIX / "wrfnat.idx").read_text()
    assert "MASSDEN:8 m above ground" in text
    recs = parse_idx(text)
    sel = select_records(recs, "MASSDEN", "hybrid level", 999)
    assert all(r["level"] != "8 m above ground" for r in sel)
    assert len(sel) == 50


def test_parse_idx_lenient_skips_garbage_line(capsys):
    # "1:X:..." has >=6 colon-separated fields but a non-numeric offset, so it
    # reaches the int() conversion and must be skipped there (not filtered out
    # by the earlier <6-parts guard, which "garbage line" alone would hit).
    recs = parse_idx("1:0:d=x:VAR:lvl:f:\n1:X:d=x:VAR2:lvl:f:\n")
    assert len(recs) == 1
    out = capsys.readouterr().out
    assert "skipped 1" in out

    # The old zero-colon case: filtered out before int() is ever reached.
    recs2 = parse_idx("1:0:d=x:VAR:lvl:f:\ngarbage line\n")
    assert len(recs2) == 1


def test_byte_ranges_raises_on_duplicate_offsets():
    recs = [
        {"n": 1, "offset": 0, "var": "A", "level": "1"},
        {"n": 2, "offset": 100, "var": "B", "level": "1"},
        {"n": 3, "offset": 100, "var": "C", "level": "1"},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        byte_ranges(recs, recs)
