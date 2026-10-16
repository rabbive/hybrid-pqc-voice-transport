"""Run the frozen app's self-check unfrozen, so it can't drift from the code it checks.

It builds calls in code; a new CLI flag broke it in CI before anything here noticed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import selfcheck  # noqa: E402


# "opus" asserts libopus loads from inside the frozen bundle (sys._MEIPASS): frozen-only.
@pytest.mark.parametrize("name", sorted(set(selfcheck.CHECKS) - {"opus"}))
def test_selfcheck_passes_unfrozen(name):
    assert selfcheck.CHECKS[name]()
