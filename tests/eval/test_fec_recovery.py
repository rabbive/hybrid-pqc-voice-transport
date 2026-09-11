import pytest

try:
    import opuslib  # noqa: F401
    _HAS_OPUS = True
except (ImportError, OSError):
    _HAS_OPUS = False

pytestmark = pytest.mark.skipif(
    not _HAS_OPUS, reason="needs libopus — run in the hpqv-eval container",
)

from hpqv.eval import fec_recovery as fr


def test_no_loss_fec_costs_nothing():
    """With no loss there is nothing to recover, so FEC-on and FEC-off should
    perform about the same (FEC only spends bits, no concealment kicks in)."""
    row = fr.measure(loss_pct=0, seconds=1.0)
    assert row["frames_lost"] == 0
    assert row["frames_recovered_by_fec"] == 0
    assert abs(row["snr_fec_on_db"] - row["snr_fec_off_db"]) < 1.0


def test_moderate_loss_fec_recovers_frames_and_improves_snr():
    """The point of the whole experiment, measured."""
    row = fr.measure(loss_pct=15, seed=1, seconds=3.0)
    assert row["frames_lost"] > 0
    assert row["frames_recovered_by_fec"] > 0
    assert row["snr_fec_on_db"] > row["snr_fec_off_db"]


def test_fec_off_degrades_monotonically_with_loss():
    """More loss cannot mean better quality — fixed seed, so deterministic."""
    rows = [fr.measure(loss_pct=p, seed=0, seconds=3.0) for p in [0, 5, 10, 20, 30]]
    snrs = [r["snr_fec_off_db"] for r in rows]
    assert snrs == sorted(snrs, reverse=True)
