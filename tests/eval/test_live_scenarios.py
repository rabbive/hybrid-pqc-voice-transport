import numpy as np

from hpqv.audio import FRAME_SAMPLES
from hpqv.eval import live_scenarios


def test_alignment_search_recovers_leading_frames():
    frame = FRAME_SAMPLES
    reference = np.arange(frame * 40, dtype=np.int16)
    shifted = reference[7 * frame:]

    aligned_ref, aligned_out, offset, _ref_start, _out_start = live_scenarios._align_frames(
        reference, shifted)

    assert offset == 7
    assert live_scenarios._whole_signal_snr_db(aligned_ref, aligned_out) >= 60


def test_live_loopback_clean_and_fec_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(live_scenarios, "RESULTS_DIR", tmp_path)
    clean = live_scenarios.run_scenario("clean", {}, 2)
    wav = tmp_path / "speech_like.wav"
    fec = live_scenarios.run_scenario(
        "test-fec", {"loss_pct": 20, "repair": "fec"}, 2, wav, seed=42)
    silence = live_scenarios.run_scenario(
        "test-silence", {"loss_pct": 20, "repair": "silence"}, 2, wav, seed=42)
    plc = live_scenarios.run_scenario(
        "test-plc", {"loss_pct": 20, "repair": "plc"}, 2, wav, seed=42)
    delay = live_scenarios.run_scenario(
        "test-delay", {"delay_ms": 100, "jitter_ms": 30, "jitter_depth": 6,
                       "repair": "fec", "fec": True}, 2, wav, seed=20261015)

    assert clean["frames_delivered"] >= clean["frames_offered"] * 0.98
    assert clean["residual_loss_pct"] < 1
    assert clean["whole_signal_snr_db"] >= 60
    assert fec["frames_recovered"] > 0
    assert fec["residual_loss_pct"] < silence["residual_loss_pct"]
    assert fec["repaired_frame_snr_db"] > silence["repaired_frame_snr_db"]
    assert plc["repaired_frame_snr_db"] is not None
    assert delay["frames_late"] == 0
    assert delay["whole_signal_snr_db"] >= 60


def test_lbrr_coverage_counts_encoded_packets(tmp_path):
    wav = live_scenarios.make_speech_wav(tmp_path / "s.wav", seconds=1.0)
    with_lbrr, packets = live_scenarios.lbrr_coverage(wav)
    assert packets == 50
    assert 0 < with_lbrr <= packets
