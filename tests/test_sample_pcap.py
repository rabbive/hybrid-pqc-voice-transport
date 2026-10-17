"""The no-root Wireshark fallback: tools/wireshark/make_sample_pcap.py must still run."""
import subprocess
import sys
from pathlib import Path

from scapy.layers.inet import UDP
from scapy.utils import rdpcap

from test_pair_cli import ENV

TOOL = Path(__file__).resolve().parent.parent / "tools" / "wireshark" / "make_sample_pcap.py"


def test_make_sample_pcap_writes_a_capture_with_voice_datagrams(tmp_path):
    out = tmp_path / "sample.pcap"
    proc = subprocess.run([sys.executable, str(TOOL), str(out)], env=ENV,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    voice = [p for p in rdpcap(str(out)) if UDP in p]
    assert len(voice) == 100
    assert max(len(p[UDP].payload) for p in voice) <= 1200
