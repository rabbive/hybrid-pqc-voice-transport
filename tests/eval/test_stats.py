from hpqv.eval import stats

def test_drop_rate():
    assert stats.drop_rate(10, [0,1,2,3,4,5,6,7]) == 0.2   # 2 of 10 lost

def test_jitter_zero_for_perfect_spacing():
    arrivals = [i*0.02 for i in range(10)]                 # perfect 20ms
    assert stats.mean_jitter_ms(arrivals) < 0.001

def test_jitter_positive_for_irregular():
    arrivals = [0.0, 0.02, 0.05, 0.06, 0.10]
    assert stats.mean_jitter_ms(arrivals) > 0

def test_ttfb():
    assert stats.ttfb_ms(1.0, 1.25) == 250.0
