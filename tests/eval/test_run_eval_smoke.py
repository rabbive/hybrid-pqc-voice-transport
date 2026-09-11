import csv
import os
import sys

import shutil, pytest
pytestmark = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from run_eval import main


def test_smoke(tmp_path, monkeypatch):
# Runs into a tmp cwd: main() writes results/ relative to the working
# directory, so without this the smoke run would overwrite the real
# evaluation results (which are committed) with 2-point quick data.
    monkeypatch.chdir(tmp_path)
    main(quick=True, losses=[0, 20])

    with open("results/eval.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0

    by_key = {(r["name"], r["loss_pct"]): r for r in rows}
    assert int(by_key[("hybrid", "0")]["frag_count"]) == 0
    assert int(by_key[("naive", "0")]["frag_count"]) > 0

    with open("results/eval_latency.csv") as f:
        latency_rows = list(csv.DictReader(f))
    assert len(latency_rows) > 0
    assert len({r["delay_ms"] for r in latency_rows}) > 1

    for png in ("loss_vs_mos.png", "loss_vs_fragments.png", "latency_vs_ttfb.png"):
        path = os.path.join("results", png)
        assert os.path.getsize(path) > 0
