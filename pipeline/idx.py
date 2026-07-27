# pipeline/idx.py — GRIB .idx sidecar parsing and byte-range planning.
# idx line: "N:OFFSET:d=YYYYMMDDHH:VAR:LEVEL:FCST[:...]"


def parse_idx(text):
    recs = []
    skipped = 0
    for line in text.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            continue
        try:
            recs.append({"n": int(parts[0]), "offset": int(parts[1]),
                         "var": parts[3], "level": parts[4]})
        except ValueError:
            skipped += 1
    if skipped:
        print(f"parse_idx: skipped {skipped} malformed line(s)")
    return recs


def select_records(recs, var, level_kind, max_levels):
    """MASSDEN/HGT on 'N hybrid level' (N <= max_levels), or exact matches like 'surface'.

    max_levels is a level ceiling when level_kind == "hybrid level" (keep every
    record whose level number is <= max_levels, sorted ascending by level); for
    exact-match level kinds (e.g. "surface") it is instead a first-N truncation
    of the matches found, in file order.
    """
    out = []
    for r in recs:
        if r["var"] != var:
            continue
        if level_kind == "hybrid level":
            parts = r["level"].split(" ", 1)
            if len(parts) == 2 and parts[1] == "hybrid level" and parts[0].isdigit() and int(parts[0]) <= max_levels:
                out.append(r)
        elif r["level"] == level_kind:
            out.append(r)
    if level_kind == "hybrid level":
        out.sort(key=lambda r: int(r["level"].split(" ")[0]))
        return out
    return out[:max_levels]


def byte_ranges(selected, all_recs):
    """(start, end_exclusive) per record; end None for the file's final record."""
    offsets = sorted(r["offset"] for r in all_recs)
    if len(set(offsets)) != len(offsets):
        raise ValueError("duplicate offsets in idx")
    nxt = {off: (offsets[i + 1] if i + 1 < len(offsets) else None) for i, off in enumerate(offsets)}
    return [(r["offset"], nxt[r["offset"]]) for r in selected]
