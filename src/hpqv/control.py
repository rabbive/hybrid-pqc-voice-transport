"""Control-plane message types. Framing and transport live in secure.SecureChannel."""

# secure.MSG_READY must equal MSG_READY (tests/test_control.py checks it).
MSG_CALL_START, MSG_CALL_END, MSG_KEY_ROTATE, MSG_READY = 1, 2, 3, 4
