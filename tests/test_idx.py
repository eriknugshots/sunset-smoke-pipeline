from pathlib import Path

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


def test_parse_idx_lenient_skips_garbage_line():
    recs = parse_idx("1:0:d=x:VAR:lvl:f:\ngarbage line\n")
    assert len(recs) == 1
