"""Pure GUI logic: presets, slider mapping, stats text. No Tk needed."""
import wave

import pytest

from hpqv.controls import CallControls, DEFAULTS
from hpqv.gui_logic import (NETWORK_KNOBS, PRESETS, SLIDERS, CallClock, end_text,
                            handshake_rows, preset_values, rotate_button_state,
                            snapshot_for_export, stat_tiles, validate_wav)

SPEC = {s.knob: s for s in SLIDERS}


@pytest.mark.parametrize("name", list(PRESETS))
def test_every_preset_sets_only_network_knobs_and_survives_clamping_unchanged(name):
    values = preset_values(name)
    assert set(values) == set(NETWORK_KNOBS)     # complete for the network, silent on repair/fec/bitrate
    assert not {"repair", "fec", "bitrate"} & set(values)
    assert CallControls(**values).snapshot() == {**DEFAULTS, **values}


@pytest.mark.parametrize("name", list(PRESETS))
def test_applying_a_preset_leaves_repair_fec_and_bitrate_alone(name):
    c = CallControls(repair="plc", fec=False, bitrate=24000)
    c.set(**preset_values(name))
    assert (c.get("repair"), c.get("fec"), c.get("bitrate")) == ("plc", False, 24000)


def test_clean_resets_every_network_knob_to_its_default():
    c = CallControls(loss_pct=30, loss_model="burst", burst_len=8, delay_ms=200, jitter_ms=90,
                     incoming_loss_pct=10, jitter_depth=9)
    c.set(**preset_values("Clean"))
    assert {k: c.get(k) for k in NETWORK_KNOBS} == {k: DEFAULTS[k] for k in NETWORK_KNOBS}


def test_preset_contents_match_the_spec():
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
    assert tiles["Loss you hear"] == tiles["Residual loss"] == tiles["MOS (audio you hear)"] == "—"


def test_stat_tiles_with_a_row():
    stats = dict(sent=0, received=0, dropped=0, incoming_dropped=0, late=0, recovered=0, concealed=0)
    row = {"interval_loss_pct": 12.34, "residual_loss_pct": 3.0, "mos": 3.456}
    tiles = dict(stat_tiles(stats, row))
    assert tiles["Loss you hear"] == "12.3 %"
    assert tiles["Residual loss"] == "3.0 %"
    assert tiles["MOS (audio you hear)"] == "3.46"


INFO = {"kem": "ML-KEM-768", "sig": "ML-DSA-65", "hello_bytes": 3000, "accept_bytes": 3400,
        "total_bytes": 6400, "transport": "TCP", "udp_fragments": 0, "datagram_cap": 1200}


def test_handshake_rows():
    rows = dict(handshake_rows(INFO, {"handshake_ms": 12.34, "max_datagram": 173}))
    assert rows["Key exchange"] == "ML-KEM-768" and rows["Signature"] == "ML-DSA-65"
    assert rows["HELLO / ACCEPT / total"] == "3000 / 3400 / 6400 B"
    assert rows["Transport"] == "TCP" and rows["UDP fragments"] == "0 (by design: handshake is on TCP)"
    assert rows["Handshake time"] == "12.3 ms"
    assert rows["Largest datagram"] == "173 B  (cap 1200 B)"


def test_handshake_rows_before_a_call_and_without_oqs():
    rows = dict(handshake_rows(None, {"handshake_ms": 0.0, "max_datagram": 0}))
    assert rows["Handshake time"] == "—" and rows["Largest datagram"] == "—"
    assert rows["Key exchange"] == "unavailable"


def test_loss_slider_captions_say_whose_audio_is_affected():
    assert "applied to audio the peer hears" in SPEC["loss_pct"].label
    assert "applied to audio you hear" in SPEC["incoming_loss_pct"].label


# ---------------- WAV validation (I3) ----------------

def _wav(path, rate=48000, channels=1, width=2, frames=960):
    with wave.open(str(path), "wb") as w:
        w.setframerate(rate)
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.writeframes(b"\0" * frames * channels * width)
    return str(path)


def test_validate_wav_accepts_48k_mono_16bit(tmp_path):
    assert validate_wav(_wav(tmp_path / "ok.wav")) is None


@pytest.mark.parametrize("kw, needle", [(dict(rate=16000), "16 kHz"),
                                        (dict(channels=2), "2 channel"),
                                        (dict(width=1), "8-bit")])
def test_validate_wav_rejects_wrong_format_and_says_what_it_found(tmp_path, kw, needle):
    msg = validate_wav(_wav(tmp_path / "bad.wav", **kw))
    assert "48 kHz mono 16-bit" in msg and needle in msg


def test_validate_wav_reports_missing_and_non_wav_files(tmp_path):
    assert validate_wav(str(tmp_path / "nope.wav"))
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav at all")
    assert "WAV" in validate_wav(str(junk))
    assert validate_wav(str(tmp_path))            # a directory


# ---------------- honest call-end text (I7) ----------------

def test_end_text_only_claims_peer_when_the_peer_ended_it():
    assert end_text(hung_up=True, peer_ended=True) == "hung up"
    assert end_text(hung_up=False, peer_ended=True) == "call ended by peer"
    assert end_text(hung_up=False, peer_ended=False) == "call ended"


# ---------------- rotate button (I8) ----------------

def test_rotate_button_waits_for_the_old_key_to_retire():
    assert rotate_button_state(live=True, ready=True) == (True, "Rotate keys")
    enabled, text = rotate_button_state(live=True, ready=False)
    assert not enabled and "old key retiring" in text.lower()
    assert rotate_button_state(live=False, ready=True)[0] is False


# ---------------- graph clock (I4) ----------------

def test_call_clock_freezes_at_hang_up_until_the_next_start():
    t = [100.0]
    cc = CallClock(lambda: t[0])
    cc.start()
    t[0] = 112.0
    assert cc.now() == 12.0
    cc.freeze()
    t[0] = 500.0
    assert cc.now() == 12.0
    cc.freeze()                                   # idempotent
    assert cc.now() == 12.0
    cc.start()
    assert cc.now() == 0.0
    t[0] = 503.0
    assert cc.now() == 3.0


# ---------------- export snapshot (I6) ----------------

class _Tel:
    def __init__(self):
        self.rows, self.events, self.marker = [{"t_s": 1.0}], [{"t": 1.0}], "kept"


def test_snapshot_for_export_is_independent_of_later_samples():
    live = _Tel()
    snap = snapshot_for_export(live)
    live.rows.append({"t_s": 2.0})
    live.events.clear()
    assert snap.rows == [{"t_s": 1.0}] and snap.events == [{"t": 1.0}]
    assert snap.marker == "kept" and snap is not live
