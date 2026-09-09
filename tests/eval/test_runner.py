from hpqv.eval.runner import run_scenario


def test_hybrid_zero_loss_no_fragments():
    r = run_scenario("hybrid", loss_pct=0, delay_ms=0, jitter_ms=0, n_frames=50)
    assert r["frag_count"] == 0          # fragmentation-free
    assert r["mos"] > 4.0
    assert r["name"] == "hybrid"


def test_naive_fragments():
    r = run_scenario("naive", loss_pct=0, delay_ms=0, jitter_ms=0, n_frames=50)
    assert r["frag_count"] > 0           # the villain fragments


def test_tcp_reliable_no_fragments():
    r = run_scenario("tcp", loss_pct=0, delay_ms=0, jitter_ms=0, n_frames=50)
    assert r["frag_count"] == 0
    assert r["drop_rate"] == 0.0
