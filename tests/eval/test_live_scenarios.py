from hpqv.eval import live_scenarios


def test_live_loopback_clean_and_fec_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(live_scenarios, "RESULTS_DIR", tmp_path)
    clean = live_scenarios.run_scenario("clean", {}, 2)
    wav = tmp_path / "speech_like.wav"
    fec = live_scenarios.run_scenario(
        "test-fec", {"loss_pct": 30, "repair": "fec"}, 2, wav, seed=42)
    silence = live_scenarios.run_scenario(
        "test-silence", {"loss_pct": 30, "repair": "silence"}, 2, wav, seed=42)

    assert clean["frames_delivered"] >= clean["frames_offered"] * 0.98
    assert clean["residual_loss_pct"] < 1
    assert silence["segmental_snr_db"] < clean["segmental_snr_db"]
    assert fec["frames_recovered"] > 0
    assert fec["residual_loss_pct"] < silence["residual_loss_pct"]
