from hpqv.eval import pqc_cost


def test_handshake_bytes_realistic():
    b = pqc_cost.handshake_bytes("hybrid")
    assert 9000 < b < 13000            # ~10.8 KB of PQC material


def test_handshake_bytes_same_for_all_schemes():
    b = pqc_cost.handshake_bytes("hybrid")
    assert pqc_cost.handshake_bytes("tcp") == b
    assert pqc_cost.handshake_bytes("quic") == b


def test_handshake_ms_positive_and_small():
    ms = pqc_cost.handshake_ms()
    assert 0 < ms < 200                # PQC handshake is sub-ms..few-ms
