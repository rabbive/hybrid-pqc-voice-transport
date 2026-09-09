import shutil, pytest
pytestmark = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)

from scapy.all import IP, UDP, fragment, wrpcap
from hpqv.eval.capture import count_ip_fragments


def test_counts_fragments(tmp_path):
    pkt = IP(dst="10.0.0.1") / UDP(dport=5000) / (b"x" * 4000)   # >MTU
    frags = fragment(pkt, fragsize=1400)                          # 3 fragments
    p = tmp_path / "frag.pcap"; wrpcap(str(p), frags)
    assert count_ip_fragments(str(p)) == len(frags)


def test_zero_fragments(tmp_path):
    pkt = IP(dst="10.0.0.1")/UDP(dport=5000)/(b"x"*200)
    p = tmp_path / "ok.pcap"; wrpcap(str(p), [pkt])
    assert count_ip_fragments(str(p)) == 0
