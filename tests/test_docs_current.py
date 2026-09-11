"""The docs must agree with the committed results.

Hand-copied numbers rot: an earlier version of the evaluation report quoted a
sweep that had since been re-run, and every cell in it was wrong. The result
tables are generated from results/*.csv, and this test fails if anyone edits a
generated block by hand or re-runs the sweeps without re-rendering.

Fix a failure with:  python scripts/render_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from render_tables import render


def test_docs_match_results():
    stale = render(check=True)
    assert not stale, (
        "These docs no longer match results/*.csv: "
        + ", ".join(stale)
        + " — run `python scripts/render_tables.py`"
    )
