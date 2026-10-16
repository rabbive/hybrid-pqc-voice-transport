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


def test_secure_check_is_registered_and_fails_on_unmirrored_keys(monkeypatch):
    """The v2 check must actually be able to fail: break the key mirroring and expect an error."""
    from hpqv import secure
    assert "secure" in selfcheck.CHECKS
    real = secure.SecureChannel.media_keys

    def swapped(self):
        keys = real(self)
        return secure.MediaKeys(keys.send_secret, keys.send_secret, keys.session_id)

    monkeypatch.setattr(secure.SecureChannel, "media_keys", swapped)
    with pytest.raises(RuntimeError, match="not mirrored"):
        selfcheck.secure_session()
