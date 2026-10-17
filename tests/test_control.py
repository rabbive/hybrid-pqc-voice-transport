"""control.py is constants only in v2; framing lives in secure.SecureChannel."""
from hpqv import control, secure


def test_message_types_are_fixed_and_distinct():
    assert (control.MSG_CALL_START, control.MSG_CALL_END,
            control.MSG_KEY_ROTATE, control.MSG_READY) == (1, 2, 3, 4)


def test_ready_matches_secure():
    assert control.MSG_READY == secure.MSG_READY


def test_v1_raw_framing_is_gone():
    for name in ("send_record", "recv_record", "send_control", "recv_control",
                 "run_initiator", "run_responder"):
        assert not hasattr(control, name), name
