"""Pure GUI logic: presets, slider mapping, stats text. No Tk needed."""
import pytest

from hpqv.controls import CallControls, DEFAULTS
from hpqv.gui_logic import (PRESETS, SLIDERS, handshake_rows, preset_values,
                            stat_tiles)

SPEC = {s.knob: s for s in SLIDERS}


@pytest.mark.parametrize("name", list(PRESETS))
def test_every_preset_is_valid_and_survives_clamping_unchanged(name):
    values = preset_values(name)
    assert set(values) == set(DEFAULTS)          # complete: a preset never leaves stale knobs
    assert CallControls(**values).snapshot() == values


def test_preset_contents_match_the_spec():
    assert preset_values("Clean") == DEFAULTS
    wifi = preset_values("Bad Wi-Fi")
    assert (wifi["loss_pct"], wifi["loss_model"], wifi["delay_ms"], wifi["jitter_ms"]) == (5, "random", 40, 20)
    burst = preset_values("Burst loss")
    assert (burst["loss_pct"], burst["loss_model"], burst["burst_len"]) == (10, "burst", 4)
    cong = preset_values("Congested")
    assert (cong["loss_pct"], cong["delay_ms"], cong["jitter_ms"], cong["jitter_depth"]) == (20, 150, 60, 6)


def test_preset_values_returns_a_copy():
    preset_values("Clean")["loss_pct"] = 99
    assert PRESETS["Clean"] == {} and preset_values("Clean")["loss_pct"] == 0


def test_slider_ranges_match_the_spec():
    ranges = {k: (s.lo, s.hi) for k, s in SPEC.items()}
    assert ranges["loss_pct"] == (0, 50) and ranges["incoming_loss_pct"] == (0, 50)
    assert ranges["burst_len"] == (1, 10) and ranges["delay_ms"] == (0, 500)
    assert ranges["jitter_ms"] == (0, 200) and ranges["jitter_depth"] == (1, 15)


def test_slider_round_trip_through_controls():
    for knob, spec in SPEC.items():
        for raw in (spec.lo, (spec.lo + spec.hi) / 2, spec.hi):
            c = CallControls(**{knob: spec.to_knob(raw)})
            assert spec.to_knob(spec.to_slider(c.get(knob))) == c.get(knob), knob


def test_jitter_depth_label_shows_milliseconds():
    assert SPEC["jitter_depth"].fmt(5) == "5 frames = 100 ms"
    assert SPEC["jitter_depth"].fmt(1) == "1 frame = 20 ms"


def test_bitrate_slider_has_an_auto_stop():
    b = SPEC["bitrate"]
    assert b.to_knob(b.lo) == 0 and b.fmt(0) == "Auto"
    assert b.to_knob(24.2) == 24000 and b.fmt(24000) == "24 kbit/s"
    assert b.to_knob(64) == 64000
    assert b.to_slider(0) == b.lo and b.to_slider(16000) == 16


def test_stat_tiles_without_a_telemetry_row():
    tiles = dict(stat_tiles({"sent": 5, "received": 4, "dropped": 1, "incoming_dropped": 0,
                             "late": 2, "recovered": 3, "concealed": 1}, None))
    assert tiles["Sent"] == "5" and tiles["Recovered (FEC)"] == "3"
    assert tiles["Measured loss"] == tiles["Residual loss"] == tiles["MOS"] == "—"


def test_stat_tiles_with_a_row():
    stats = dict(sent=0, received=0, dropped=0, incoming_dropped=0, late=0, recovered=0, concealed=0)
    row = {"interval_loss_pct": 12.34, "residual_loss_pct": 3.0, "mos": 3.456}
    tiles = dict(stat_tiles(stats, row))
    assert tiles["Measured loss"] == "12.3 %"
    assert tiles["Residual loss"] == "3.0 %"
    assert tiles["MOS"] == "3.46"


INFO = {"kem": "ML-KEM-768", "sig": "ML-DSA-65", "hello_bytes": 3000, "accept_bytes": 3400,
        "total_bytes": 6400, "transport": "TCP", "udp_fragments": 0, "datagram_cap": 1200}


def test_handshake_rows():
    rows = dict(handshake_rows(INFO, {"handshake_ms": 12.34, "max_datagram": 173}))
    assert rows["Key exchange"] == "ML-KEM-768" and rows["Signature"] == "ML-DSA-65"
    assert rows["HELLO / ACCEPT / total"] == "3000 / 3400 / 6400 B"
    assert rows["Transport"] == "TCP" and rows["UDP fragments"] == "0"
    assert rows["Handshake time"] == "12.3 ms"
    assert rows["Largest datagram"] == "173 B  (cap 1200 B)"


def test_handshake_rows_before_a_call_and_without_oqs():
    rows = dict(handshake_rows(None, {"handshake_ms": 0.0, "max_datagram": 0}))
    assert rows["Handshake time"] == "—" and rows["Largest datagram"] == "—"
    assert rows["Key exchange"] == "unavailable"
