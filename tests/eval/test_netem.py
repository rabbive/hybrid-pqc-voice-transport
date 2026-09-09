import subprocess

import shutil, pytest
pytestmark = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)

from hpqv.eval.netem import netem

def test_netem_applies_and_clears():
    with netem(loss_pct=10, delay_ms=50, dev="lo"):
        out = subprocess.run(["tc","qdisc","show","dev","lo"],
                             capture_output=True, text=True).stdout
        assert "netem" in out and "loss 10%" in out
    out = subprocess.run(["tc","qdisc","show","dev","lo"],
                         capture_output=True, text=True).stdout
    assert "netem" not in out            # cleared on exit
