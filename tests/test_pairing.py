"""B2 no-files pairing: protocol, window, trust store, listener, separation from calls.

Real TLS 1.3 over loopback sockets; fakes only for the human (ask_user) and for
forced I/O failures. See docs/reviews/2026-10-16-b2-pairing-design.md.
"""
import json
import os
import socket
import stat
import threading
import time

import pytest

from hpqv import identity, pairing, secure
from hpqv.callctl import CallHandle
from hpqv.demo import Stats
from hpqv.listener import Listener
from hpqv.pairing import PairingFailed, PairingWindow
from hpqv.secure import HandshakeError, SecureChannel
from hpqv.trust import TrustStore, TrustStoreError

T = 5.0
ALICE, BOB, MALLORY = identity.new("alice"), identity.new("bob"), identity.new("mallory")


def yes(*_):
    return True


def no(*_):
    return False


def _pair_channels(client_ident=BOB, server_ident=ALICE, accept_pairing=True,
                   server_pins=(), mode="pair", client_pins=None):
    ls = socket.create_server(("127.0.0.1", 0))
    out = {}

    def serve():
        s, _ = ls.accept()
        try:
            out["s"] = SecureChannel.establish(s, "server", server_ident, list(server_pins), T,
                                               accept_pairing=accept_pairing)
        except Exception as e:
            out["s"] = e
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    c = socket.create_connection(ls.getsockname())
    try:
        out["c"] = SecureChannel.establish(c, "client", client_ident, client_pins, T, mode=mode)
    except Exception as e:
        out["c"] = e
    t.join(T + 2)
    ls.close()
    return out["c"], out["s"]


def _run_both(c, s, ask_c=yes, ask_s=yes, store_c=None, store_s=None, **kw):
    res = {}

    def go(name, ch, ask, store):
        try:
            res[name] = pairing.run(ch, store, ask, **kw)
        except Exception as e:
            res[name] = e
        finally:
            ch.close()
    threads = [threading.Thread(target=go, args=("c", c, ask_c, store_c)),
               threading.Thread(target=go, args=("s", s, ask_s, store_s))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "pairing hung"
    return res["c"], res["s"]


@pytest.fixture
def stores(tmp_path):
    return TrustStore(tmp_path / "bob.json"), TrustStore(tmp_path / "alice.json")


def _reason(x):
    assert isinstance(x, PairingFailed), x
    return x.reason


# -- success, and the handoff to a pinned call -------------------------------------------

def test_successful_pairing_saves_both_and_enables_a_pinned_call(stores):
    bob_store, alice_store = stores
    seen = {}

    def ask(name):
        def f(code, label, fp, key_changed, cancel):
            seen[name] = (code, label, key_changed)
            return True
        return f
    c, s = _pair_channels()
    rc, rs = _run_both(c, s, ask("bob"), ask("alice"), bob_store, alice_store)
    assert rc.code == rs.code and seen["bob"][0] == seen["alice"][0]
    assert seen["bob"][1] == "alice" and seen["alice"][1] == "bob"
    assert not seen["bob"][2] and not seen["alice"][2]
    assert [f.fingerprint for f in bob_store.friends()] == [identity.fingerprint(ALICE.cert_pem)]
    assert [f.fingerprint for f in alice_store.friends()] == [identity.fingerprint(BOB.cert_pem)]
    # The stored pins now authorise a normal call on the unchanged B1 path.
    call_c, call_s = _pair_channels(mode="call", client_pins=bob_store.trusted_certs(),
                                    server_pins=alice_store.trusted_certs(), accept_pairing=False)
    try:
        assert call_c.mode == call_s.mode == "call"
        assert call_c.media_keys().send_secret == call_s.media_keys().recv_secret
    finally:
        call_c.close()
        call_s.close()


def test_code_format():
    code = pairing.comparison_code(bytes(32), bytes(32), bytes(32), bytes(32), bytes(32))
    assert len(code) == 7 and code[3] == " " and code.replace(" ", "").isdigit()


# -- pairing can never become a call -------------------------------------------------------

def test_pairing_channel_has_no_voice_keys_and_cannot_start_a_call():
    c, s = _pair_channels()
    try:
        assert c.mode == s.mode == "pair"
        for ch in (c, s):
            with pytest.raises(RuntimeError, match="no voice keys"):
                ch.media_keys()
            with pytest.raises(RuntimeError, match="cannot carry a call"):
                CallHandle().start(ch, Stats(), threading.Event())
    finally:
        c.close()
        s.close()


def test_unknown_key_cannot_call_even_while_pairing_is_open():
    c, s = _pair_channels(mode="call", client_pins=[ALICE.cert_pem], server_pins=[],
                          accept_pairing=True)
    assert isinstance(s, HandshakeError) and s.reason == "bad_peer_cert"
    assert isinstance(c, HandshakeError)


def test_pairing_refused_when_server_does_not_accept_pairing():
    c, s = _pair_channels(accept_pairing=False, server_pins=[BOB.cert_pem])
    assert isinstance(s, HandshakeError) and isinstance(c, HandshakeError)


# -- mismatched codes and rejection --------------------------------------------------------

@pytest.mark.parametrize("who", ["client", "server"])
def test_one_user_rejects_nothing_saved_on_either_side(stores, who):
    bob_store, alice_store = stores
    c, s = _pair_channels()
    rc, rs = _run_both(c, s, no if who == "client" else yes, no if who == "server" else yes,
                       bob_store, alice_store)
    assert {_reason(rc), _reason(rs)} == {"declined", "peer_declined"}
    assert bob_store.friends() == [] and alice_store.friends() == []
    assert rc.mismatch and rs.mismatch


def test_bad_commitment_aborts_and_saves_nothing(stores, monkeypatch):
    """A responder that reveals a nonce other than the committed one is caught."""
    bob_store, alice_store = stores
    real_send = SecureChannel.send

    def tamper(self, msg_type, payload=b""):
        if self.role == "server" and msg_type == pairing.MSG_REVEAL:
            payload = bytes(b ^ 1 for b in payload)
        return real_send(self, msg_type, payload)
    monkeypatch.setattr(SecureChannel, "send", tamper)
    c, s = _pair_channels()
    rc, rs = _run_both(c, s, yes, yes, bob_store, alice_store, step_timeout=2)
    assert _reason(rc) == "bad_commit" and rc.mismatch
    assert isinstance(rs, PairingFailed)
    assert bob_store.friends() == [] and alice_store.friends() == []


def test_initiator_draws_its_nonce_only_after_the_commitment(stores, monkeypatch):
    order = []
    real_recv, real_token = pairing._recv, pairing.secrets.token_bytes

    def recv(channel, msg_type, length, timeout):
        out = real_recv(channel, msg_type, length, timeout)
        order.append(("recv", channel.role, msg_type))
        return out

    def token(n):
        order.append(("draw", threading.current_thread().name))
        return real_token(n)
    monkeypatch.setattr(pairing, "_recv", recv)
    monkeypatch.setattr(pairing.secrets, "token_bytes", token)
    c, s = _pair_channels()
    _run_both(c, s, yes, yes, *stores)
    client_events = [e for e in order if e[0] == "recv" and e[1] == "client"]
    assert client_events[0] == ("recv", "client", pairing.MSG_COMMIT)
    first_commit = order.index(("recv", "client", pairing.MSG_COMMIT))
    draws_after = [e for e in order[first_commit:] if e[0] == "draw"]
    assert draws_after, "initiator nonce was drawn before COMMIT arrived"


def test_relay_attacker_with_two_sessions_gets_different_codes(stores):
    """Mallory terminates TLS with Bob (as responder) and with Alice (as initiator)
    and runs the protocol honestly on each side. Each session has its own channel
    binding, so the codes differ (equal only with probability ~1e-6 per run)."""
    matches = 0
    for _ in range(5):
        codes = {}
        b_c, m_s = _pair_channels(client_ident=BOB, server_ident=MALLORY)
        m_c, a_s = _pair_channels(client_ident=MALLORY, server_ident=ALICE)

        def ask(name):
            def f(code, *_):
                codes[name] = code
                return False                   # users notice and say no
            return f
        _run_both(b_c, m_s, ask("bob"), ask("m1"), *stores)
        _run_both(m_c, a_s, ask("m2"), ask("alice"), *stores)
        matches += codes["bob"] == codes["alice"]
    assert matches == 0


# -- rejected identities -------------------------------------------------------------------

def test_pairing_with_own_identity_is_refused():
    c, s = _pair_channels(client_ident=ALICE, server_ident=ALICE)
    assert isinstance(c, HandshakeError) and isinstance(s, HandshakeError)


def test_changed_key_under_known_name_is_flagged_and_old_friend_kept(stores, tmp_path):
    bob_store, alice_store = stores
    c, s = _pair_channels()
    _run_both(c, s, yes, yes, bob_store, alice_store)
    new_bob = identity.new("bob")                 # same name, different key
    flags = {}

    def ask_alice(code, label, fp, key_changed, cancel):
        flags["key_changed"] = key_changed
        return True
    c, s = _pair_channels(client_ident=new_bob)
    _run_both(c, s, yes, ask_alice, TrustStore(tmp_path / "newbob.json"), alice_store)
    assert flags["key_changed"] is True
    fps = {f.fingerprint for f in alice_store.by_label("bob")}
    assert fps == {identity.fingerprint(BOB.cert_pem), identity.fingerprint(new_bob.cert_pem)}


def test_repairing_same_key_refreshes_without_duplicates(stores):
    bob_store, alice_store = stores
    for _ in range(2):
        c, s = _pair_channels()
        _run_both(c, s, yes, yes, bob_store, alice_store)
    assert len(alice_store.friends()) == 1 and len(bob_store.friends()) == 1


# -- interruption, timeout, cleanup --------------------------------------------------------

def test_disconnect_after_commit_saves_nothing_and_wipes_nonces(stores, monkeypatch):
    bob_store, alice_store = stores
    buffers = []
    real_bytearray = bytearray

    class Spy(real_bytearray):
        def __init__(self, *a):
            super().__init__(*a)
            buffers.append(self)
    monkeypatch.setattr(pairing, "bytearray", Spy, raising=False)
    c, s = _pair_channels()
    res = {}

    def server():
        try:
            pairing._send(s, pairing.MSG_COMMIT, os.urandom(32))
        finally:
            s.close()                          # vanish before REVEAL

    def client():
        try:
            res["c"] = pairing.run(c, bob_store, yes, step_timeout=3)
        except Exception as e:
            res["c"] = e
    ts = [threading.Thread(target=server), threading.Thread(target=client)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    c.close()
    assert _reason(res["c"]) in ("peer_closed", "timeout")
    assert bob_store.friends() == []
    assert buffers and all(bytes(b) == bytes(len(b)) for b in buffers)


def test_silent_peer_times_out(stores):
    c, s = _pair_channels()
    t0 = time.monotonic()
    with pytest.raises(PairingFailed) as e:
        pairing.run(c, stores[0], yes, step_timeout=0.5)
    assert e.value.reason == "timeout" and time.monotonic() - t0 < 3
    c.close()
    s.close()


def test_undecided_user_times_out_and_peer_is_told(stores):
    def never(code, label, fp, key_changed, cancel):
        cancel.wait(10)
        return True
    c, s = _pair_channels()
    res = {}

    def go(name, ch, ask, store, timeout):
        try:
            res[name] = pairing.run(ch, store, ask, decision_timeout=timeout)
        except Exception as e:
            res[name] = e
        finally:
            ch.close()
    ts = [threading.Thread(target=go, args=("c", c, never, stores[0], 1)),
          threading.Thread(target=go, args=("s", s, yes, stores[1], 10))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    rc, rs = res["c"], res["s"]
    assert _reason(rc) == "timeout" and _reason(rs) == "peer_declined"
    assert stores[0].friends() == [] and stores[1].friends() == []


def test_ask_user_exception_counts_as_no(stores):
    def boom(*_):
        raise RuntimeError("dialog crashed")
    c, s = _pair_channels()
    rc, rs = _run_both(c, s, boom, yes, *stores)
    assert _reason(rc) == "declined" and stores[1].friends() == []


# -- malformed messages --------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    (pairing.MSG_NONCE, bytes(32)),            # wrong type first
    (pairing.MSG_COMMIT, bytes(31)),           # short
    (pairing.MSG_COMMIT, bytes(33)),           # long
    (0x7F, b"junk"),                           # unknown type
])
def test_malformed_first_message_is_a_protocol_error(stores, bad):
    c, s = _pair_channels()
    s.send(*bad)
    with pytest.raises(PairingFailed) as e:
        pairing.run(c, stores[0], yes, step_timeout=2)
    assert e.value.reason == "protocol" and stores[0].friends() == []
    c.close()
    s.close()


def test_oversized_frame_is_rejected(stores):
    c, s = _pair_channels()
    with pytest.raises(ValueError):
        s.send(pairing.MSG_COMMIT, bytes(secure.MAX_CONTROL_PAYLOAD + 1))
    c.close()
    s.close()


def test_bad_confirm_byte_is_a_protocol_error(stores, monkeypatch):
    real_send = SecureChannel.send

    def odd(self, msg_type, payload=b""):
        if self.role == "server" and msg_type == pairing.MSG_CONFIRM:
            payload = b"\x02"
        return real_send(self, msg_type, payload)
    monkeypatch.setattr(SecureChannel, "send", odd)
    c, s = _pair_channels()
    rc, _ = _run_both(c, s, yes, yes, *stores)
    assert _reason(rc) == "protocol" and stores[0].friends() == []


# -- persistence failures ------------------------------------------------------------------

def test_failed_save_reports_store_failed_keeps_old_file_and_retry_works(stores, monkeypatch):
    bob_store, alice_store = stores
    c, s = _pair_channels()
    _run_both(c, s, yes, yes, bob_store, alice_store)        # one good save first
    before = alice_store.path.read_bytes()
    real_replace = os.replace

    def broken(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", broken)
    c, s = _pair_channels(client_ident=identity.new("carol"))
    _, rs = _run_both(c, s, yes, yes, TrustStore(alice_store.path.with_name("c.json")),
                      alice_store)
    assert _reason(rs) == "store_failed"
    assert alice_store.path.read_bytes() == before
    assert not list(alice_store.path.parent.glob(".trust-*.tmp"))   # temp file cleaned up
    monkeypatch.setattr(os, "replace", real_replace)
    carol = identity.new("carol")
    c, s = _pair_channels(client_ident=carol)
    _, rs = _run_both(c, s, yes, yes, TrustStore(alice_store.path.with_name("c2.json")),
                      alice_store)
    assert not isinstance(rs, Exception) and len(alice_store.friends()) == 2


def test_damaged_store_refuses_and_is_never_overwritten(stores):
    _, alice_store = stores
    alice_store.path.write_text("{not json")
    with pytest.raises(TrustStoreError):
        alice_store.friends()
    c, s = _pair_channels()
    _, rs = _run_both(c, s, yes, yes, stores[0], alice_store)
    assert _reason(rs) == "store_failed"
    assert alice_store.path.read_text() == "{not json"


def test_tampered_fingerprint_in_store_is_detected(stores):
    bob_store, alice_store = stores
    c, s = _pair_channels()
    _run_both(c, s, yes, yes, bob_store, alice_store)
    doc = json.loads(alice_store.path.read_text())
    doc["peers"][0]["fingerprint"] = "0000" + doc["peers"][0]["fingerprint"][4:]
    alice_store.path.write_text(json.dumps(doc))
    with pytest.raises(TrustStoreError):
        alice_store.friends()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_store_written_owner_only(stores):
    c, s = _pair_channels()
    _run_both(c, s, yes, yes, *stores)
    assert stat.S_IMODE(os.stat(stores[1].path).st_mode) == 0o600


# -- window: duplicate and concurrent attempts --------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_window_one_at_a_time_three_attempts_and_expiry():
    clock = Clock()
    w = PairingWindow(seconds=120, max_attempts=3, clock=clock)
    assert not w.try_begin()                       # not opened by the user
    w.open()
    assert w.try_begin() and not w.try_begin()     # one at a time
    w.finish(None)
    assert w.try_begin()
    w.finish(None)
    assert w.try_begin()
    w.finish(None)
    assert not w.try_begin() and w.attempts == 3   # a fourth is refused
    w.open()
    clock.t = 121
    assert not w.is_open() and w.closed_reason == "expired"


def test_window_closes_on_mismatch_but_not_on_timeout():
    w = PairingWindow()
    w.open()
    assert w.try_begin()
    w.finish(PairingFailed("timeout"))
    assert w.is_open()
    assert w.try_begin()
    w.finish(PairingFailed("declined"))
    assert not w.is_open() and w.closed_reason == "codes did not match"


# -- listener --------------------------------------------------------------------------

def _connect_pair(port, ident=BOB, store=None):
    sock = socket.create_connection(("127.0.0.1", port))
    ch = SecureChannel.establish(sock, "client", ident, None, T, mode="pair")
    try:
        return pairing.run(ch, store, yes, step_timeout=3)
    finally:
        ch.close()


def test_listener_pairs_through_window_and_survives_stray_connections(tmp_path):
    alice_store = TrustStore(tmp_path / "alice.json")
    window = PairingWindow()
    lst = Listener(ALICE, 0, host="127.0.0.1", trusted=alice_store.trusted_certs,
                   window=window,
                   on_pair=lambda ch, addr: pairing.run(ch, alice_store, yes, step_timeout=3),
                   handshake_timeout=2).start()
    try:
        for _ in range(100):                     # port scans / stray connects
            socket.create_connection(("127.0.0.1", lst.port)).close()
        with pytest.raises(HandshakeError):      # window closed: pairing refused
            _connect_pair(lst.port, store=TrustStore(tmp_path / "b0.json"))
        window.open()
        res = _connect_pair(lst.port, store=TrustStore(tmp_path / "bob.json"))
        assert res.friend.fingerprint == identity.fingerprint(ALICE.cert_pem)
        deadline = time.monotonic() + 3
        while not alice_store.friends() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [f.label for f in alice_store.friends()] == ["bob"]
    finally:
        lst.stop()


def test_listener_refuses_a_second_concurrent_pairing(tmp_path):
    started, release = threading.Event(), threading.Event()
    window = PairingWindow()
    window.open()

    def slow_pair(ch, addr):
        started.set()
        release.wait(5)
    lst = Listener(ALICE, 0, host="127.0.0.1", trusted=list, window=window,
                   on_pair=slow_pair).start()
    try:
        first = SecureChannel.establish(socket.create_connection(("127.0.0.1", lst.port)),
                                        "client", BOB, None, T, mode="pair")
        assert started.wait(5)
        second = SecureChannel.establish(socket.create_connection(("127.0.0.1", lst.port)),
                                         "client", MALLORY, None, T, mode="pair")
        with pytest.raises(ConnectionError):     # closed by the listener: window busy
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                second.recv(timeout=0.2)
        release.set()
        first.close()
        second.close()
    finally:
        lst.stop()
