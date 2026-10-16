import threading
import time

from hpqv.impair import DelayLine, LossModel


def _loss_stats(loss_model, loss_pct, model, burst_len=4, count=20_000):
    drops = [loss_model.drop(loss_pct, model, burst_len) for _ in range(count)]
    runs = []
    current = 0
    for dropped in drops:
        if dropped:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return sum(drops) / count, sum(runs) / len(runs)


def test_burst_loss_hits_target_average_and_mean_burst_length():
    model = LossModel(seed=42)
    loss_rate, mean_burst = _loss_stats(model, 15, "burst", 4)
    assert abs(loss_rate - 0.15) < 0.01
    assert abs(mean_burst - 4) < 0.35


def test_burst_loss_reports_and_caps_unachievable_target():
    model = LossModel(seed=11)
    assert model.effective_loss_pct(99, "burst", 4) == 80
    loss_rate, _ = _loss_stats(model, 99, "burst", 4)
    assert abs(loss_rate - 0.8) < 0.01


def test_random_loss_hits_target_and_handles_extremes():
    model = LossModel(seed=7)
    loss_rate, _ = _loss_stats(model, 23, "random")
    assert abs(loss_rate - 0.23) < 0.01
    assert not any(model.drop(0, "burst", 3) for _ in range(100))
    assert all(model.drop(100, "random", 3) for _ in range(100))


def test_delay_line_reorders_when_jitter_changes_delivery_times():
    sent = []
    delivered = {}
    done = threading.Event()

    def deliver(value):
        sent.append(value)
        delivered[value] = time.monotonic() - started
        if len(sent) == 2:
            done.set()

    started = time.monotonic()
    line = DelayLine(deliver, seed=4)
    line.send("first", delay_ms=100, jitter_ms=90)
    line.send("second", delay_ms=100, jitter_ms=90)
    try:
        assert done.wait(1)
        assert sent == ["second", "first"]
        assert all(0.01 <= elapsed <= 1.0 for elapsed in delivered.values())
    finally:
        line.close()


def test_delay_line_preserves_order_without_jitter_and_respects_deadline():
    sent = []
    delivered = []
    done = threading.Event()

    def deliver(value):
        delivered.append(time.monotonic())
        sent.append(value)
        if len(sent) == 3:
            done.set()

    started = time.monotonic()
    line = DelayLine(deliver, seed=2)
    for value in range(3):
        line.send(value, delay_ms=50)
    try:
        assert done.wait(1)
        assert sent == [0, 1, 2]
        assert all(0.04 <= at - started <= 1.0 for at in delivered)
    finally:
        line.close()


def test_delay_line_zero_delay_is_inline_and_close_cancels_pending_sends():
    sent = []
    line = DelayLine(sent.append, seed=2)
    line.send("inline")
    assert sent == ["inline"]
    assert line._thread is None
    line.send("pending", delay_ms=200)
    line.close()
    time.sleep(0.22)
    assert sent == ["inline"]
    assert not line._thread.is_alive()


def test_delay_line_propagates_delivery_errors_from_its_thread():
    failed = threading.Event()

    def fail(_value):
        failed.set()
        raise OSError("send failed")

    line = DelayLine(fail)
    line.send("delayed", delay_ms=1)
    assert failed.wait(1)
    try:
        line.close()
    except OSError as error:
        assert str(error) == "send failed"
    else:
        raise AssertionError("close must report a failed send")
