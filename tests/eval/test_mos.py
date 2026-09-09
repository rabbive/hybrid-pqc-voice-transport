from hpqv.eval import mos

def test_perfect_network_high_mos():
    m = mos.mos_from_network(delay_ms=0, jitter_ms=0, loss_pct=0)
    assert 4.0 <= m <= 4.5          # clean network ≈ 4.4 (G.107 default R≈93)

def test_loss_degrades_mos():
    good = mos.mos_from_network(0, 0, 0)
    bad = mos.mos_from_network(0, 0, 20)
    assert bad < good - 1.0         # 20% loss must drop MOS clearly

def test_mos_clamped():
    assert 1.0 <= mos.mos(-50) <= 4.5
    assert 1.0 <= mos.mos(200) <= 4.5

def test_delay_impairment_continuous_at_knee():
    assert abs(mos.r_factor(177.2, 0, 0) - mos.r_factor(177.4, 0, 0)) < 0.1

def test_higher_delay_lowers_mos():
    assert mos.mos_from_network(300, 0, 0) < mos.mos_from_network(50, 0, 0)
