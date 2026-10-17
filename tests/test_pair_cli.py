"""End-to-end B2 through the real CLI, one process per device (each its own HPQV_HOME)."""
import math
import os
import socket
import struct
import subprocess
import sys
import time
import wave

import pytest

from hpqv.audio import FRAME_SAMPLES, FS

ENV = dict(os.environ, DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib", PYTHONUNBUFFERED="1")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"nothing listening on {port}")


def _cli(home, *args, stdin=""):
    """Start the CLI with its whole stdin already written (answers are read when asked)."""
    proc = subprocess.Popen([sys.executable, "-m", "hpqv.demo", *args],
                            env=dict(ENV, HPQV_HOME=str(home)), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc.stdin.write(stdin)
    proc.stdin.close()
    return proc


def _finish(proc, timeout=90):
    out = proc.stdout.read() if proc.wait(timeout) is not None else ""
    return proc.returncode, out


def _pair(tmp_path, a_answer="y\n", b_answer="y\n", a_home="alice", b_home="bob"):
    port = _free_port()
    a = _cli(tmp_path / a_home, "pair", "--allow", "--port", str(port), "--no-discovery",
             "--seconds", "30", stdin=a_answer)
    _wait_port(port)
    b = _cli(tmp_path / b_home, "pair", f"127.0.0.1:{port}", stdin=b_answer)
    rb, ob = _finish(b)
    ra, oa = _finish(a)
    return (ra, oa), (rb, ob)


def _tone(path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        n = int(seconds * FS)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / FS)))
                               for i in range(n)))


def _frames(path):
    with wave.open(str(path)) as w:
        return w.getnframes()


def test_cli_pair_then_call_by_pinned_identity(tmp_path):
    (ra, oa), (rb, ob) = _pair(tmp_path)
    assert ra == 0 and rb == 0, (oa, ob)
    assert "paired with" in oa and "paired with" in ob
    code_a = [l for l in oa.splitlines() if "Pairing code" in l]
    code_b = [l for l in ob.splitlines() if "Pairing code" in l]
    assert code_a and code_a[0].split(":", 1)[1].strip() == code_b[0].split(":", 1)[1].strip()

    wav = tmp_path / "tone.wav"
    _tone(wav)
    port = _free_port()
    a = _cli(tmp_path / "alice", "listen", "--port", str(port), "--no-discovery",
             "--input", "file", "--wav", str(wav), "--output", "file",
             "--out-wav", str(tmp_path / "alice_heard.wav"))
    _wait_port(port)
    b = _cli(tmp_path / "bob", "call", f"127.0.0.1:{port}", "--input", "file",
             "--wav", str(wav), "--output", "file", "--out-wav", str(tmp_path / "bob_heard.wav"))
    rb, ob = _finish(b)
    ra, oa = _finish(a)
    assert ra == 0 and rb == 0, (oa, ob)
    assert "X25519MLKEM768" in oa and "X25519MLKEM768" in ob
    for heard in ("alice_heard.wav", "bob_heard.wav"):
        assert abs(_frames(tmp_path / heard) - _frames(wav)) <= 4 * FRAME_SAMPLES


@pytest.mark.parametrize("cmd", ["call", "pair"])
def test_cli_unreachable_address_exits_with_a_message_not_a_traceback(tmp_path, cmd):
    (ra, _), (rb, _) = _pair(tmp_path)
    assert ra == rb == 0
    rc, out = _finish(_cli(tmp_path / "bob", cmd, f"127.0.0.1:{_free_port()}"), timeout=30)
    assert rc != 0
    assert "Traceback" not in out
    assert "could not reach 127.0.0.1" in out


def test_cli_mismatch_saves_nothing_on_either_side(tmp_path):
    (ra, oa), (rb, ob) = _pair(tmp_path, a_answer="y\n", b_answer="n\n")
    assert ra != 0 and rb != 0, (oa, ob)
    for home in ("alice", "bob"):
        proc = _cli(tmp_path / home, "peers", "--no-discovery")
        rc, out = _finish(proc)
        assert rc == 0 and "paired friends: none" in out, out


def test_cli_unpaired_device_cannot_call(tmp_path):
    _pair(tmp_path)
    port = _free_port()
    a = _cli(tmp_path / "alice", "listen", "--port", str(port), "--no-discovery",
             "--input", "file", "--wav", str(tmp_path / "x.wav"), "--output", "file",
             "--out-wav", str(tmp_path / "o.wav"))
    _wait_port(port)
    # Mallory is paired with Carol, so she has real pins, but not with Alice.
    (rc_, _), (rm_, _) = _pair(tmp_path, a_home="carol", b_home="mallory")
    assert rc_ == 0 and rm_ == 0
    proc = _cli(tmp_path / "mallory", "call", f"127.0.0.1:{port}", "--input", "file",
                "--wav", str(tmp_path / "x.wav"), "--output", "file",
                "--out-wav", str(tmp_path / "m.wav"))
    rc, out = _finish(proc)
    assert rc != 0 and "handshake failed" in out, out
    assert a.poll() is None, "the listener must keep running after an unpaired caller"
    a.kill()
    a.wait(10)


def test_cli_forget_removes_friend(tmp_path):
    _pair(tmp_path)
    rc, out = _finish(_cli(tmp_path / "alice", "peers", "--no-discovery"))
    fp_line = [l for l in out.splitlines() if "paired 20" in l][0]
    fp_tail = fp_line.split()[1]
    rc, out = _finish(_cli(tmp_path / "alice", "forget", fp_line.split()[0]))
    assert rc == 0 and "removed" in out
    rc, out = _finish(_cli(tmp_path / "alice", "peers", "--no-discovery"))
    assert "paired friends: none" in out and fp_tail not in out
