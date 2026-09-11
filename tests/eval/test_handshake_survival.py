import shutil
import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)

from hpqv.eval import handshake_survival as hs
from hpqv.eval.netem import netem, loopback_mtu


def test_expected_fragments_matches_mtu_math():
    # 10842 bytes + 8B UDP header over 1480B fragment payloads
    assert hs.expected_fragments(10842) == 8
    assert hs.expected_fragments(100) == 1


def test_clean_link_both_transports_succeed():
    with loopback_mtu():
        assert hs.measure("naive", attempts=5)["success_pct"] == 100.0
        assert hs.measure("hybrid", attempts=5)["success_pct"] == 100.0


def test_fragmented_handshake_fails_under_loss_but_hybrid_survives():
    """The point of the whole project, measured."""
    with loopback_mtu(), netem(loss_pct=20, dev="lo"):
        naive = hs.measure("naive", attempts=20)
        hybrid = hs.measure("hybrid", attempts=20)
    # A 8-fragment datagram at 20% loss should mostly NOT arrive.
    assert naive["success_pct"] < 50.0
    # TCP retransmits, so the hybrid handshake still completes.
    assert hybrid["success_pct"] > naive["success_pct"] + 30.0
