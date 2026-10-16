import pytest

from hpqv.controls import CallControls


def test_defaults_and_clamping():
    c = CallControls(loss_pct=250, jitter_depth=0)
    assert c.get("loss_pct") == 100.0
    assert c.get("jitter_depth") == 1
    assert c.get("repair") == "fec" and c.get("fec") is True


def test_rejects_unknown_knobs_and_bad_choices():
    c = CallControls()
    with pytest.raises(KeyError):
        c.set(volume=3)
    with pytest.raises(ValueError):
        c.set(loss_model="chaos")
    assert c.get("loss_model") == "random"      # a rejected set changes nothing


def test_snapshot_is_a_copy():
    c = CallControls()
    snap = c.snapshot()
    snap["loss_pct"] = 50
    assert c.get("loss_pct") == 0.0


def test_bitrate_clamps_to_auto_or_opus_supported_range():
    c = CallControls()
    for bitrate in (1, 499):
        c.set(bitrate=bitrate)
        assert c.get("bitrate") == 500
    c.set(bitrate=-1)
    assert c.get("bitrate") == 0
    c.set(bitrate=600000)
    assert c.get("bitrate") == 512000
