import struct, zlib
import numpy as np
import pytest

from pipeline.encode import log_encode, encode_smk1


def test_log_encode_anchors():
    c = np.array([0.0, 0.1, 0.2, 300.0, 5000.0, 3.0])
    b = log_encode(c)
    assert list(b[:2]) == [0, 0]
    assert b[2] in (0, 1)                    # threshold edge may round either way
    assert b[3] == 255 and b[4] == 255
    assert 0 < b[5] < 255


def test_log_encode_monotone():
    c = np.logspace(-1, 3, 50)
    b = log_encode(c)
    assert all(b[i] <= b[i + 1] for i in range(len(b) - 1))


def test_log_encode_nan_to_zero():
    # HRRR regrids commonly produce NaN at domain edges; NaN comparisons are
    # False so `c < C_LO` alone would not catch it — pin the explicit guard.
    c = np.array([np.nan, 1.0, np.inf, -np.inf, 50.0])
    b = log_encode(c)
    assert b[0] == 0 and b[2] == 0 and b[3] == 0
    assert b[1] > 0 and b[4] > 0


def test_encode_smk1_header_and_roundtrip():
    nx, ny, nz = 4, 3, 2
    dens = np.arange(nx * ny * nz, dtype=np.uint8)
    terr = np.arange(nx * ny, dtype=np.float32) + 1000.0
    buf = encode_smk1({"west": -122.0, "south": 43.0, "east": -120.0, "north": 45.0},
                      nx, ny, nz, 0.0, 250.0, 4.0, terr, dens)
    assert buf[:4] == b"SMK1"
    w, s, e, n = struct.unpack("<4f", buf[4:20])
    assert (w, s, e, n) == (-122.0, 43.0, -120.0, 45.0)
    rnx, rny, rnz = struct.unpack("<3H", buf[20:26])
    assert (rnx, rny, rnz) == (nx, ny, nz)
    zb, zs = struct.unpack("<2f", buf[26:34])
    assert (zb, zs) == (0.0, 250.0)
    assert struct.unpack("<f", buf[34:38])[0] == 4.0
    rterr = np.frombuffer(buf[38:38 + nx * ny * 4], dtype="<f4")
    assert np.array_equal(rterr, terr)
    rdens = np.frombuffer(zlib.decompress(buf[38 + nx * ny * 4:]), dtype=np.uint8)
    assert np.array_equal(rdens, dens)


def test_encode_smk1_rejects_wrong_size_terrain():
    nx, ny, nz = 4, 3, 2
    dens = np.arange(nx * ny * nz, dtype=np.uint8)
    terr = np.arange(nx * ny - 1, dtype=np.float32) + 1000.0   # short by one
    with pytest.raises(ValueError, match="size"):
        encode_smk1({"west": -122.0, "south": 43.0, "east": -120.0, "north": 45.0},
                    nx, ny, nz, 0.0, 250.0, 4.0, terr, dens)


def test_encode_smk1_rejects_wrong_size_density():
    nx, ny, nz = 4, 3, 2
    dens = np.arange(nx * ny * nz - 1, dtype=np.uint8)          # short by one
    terr = np.arange(nx * ny, dtype=np.float32) + 1000.0
    with pytest.raises(ValueError, match="size"):
        encode_smk1({"west": -122.0, "south": 43.0, "east": -120.0, "north": 45.0},
                    nx, ny, nz, 0.0, 250.0, 4.0, terr, dens)
