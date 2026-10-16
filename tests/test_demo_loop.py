"""FrameSource file mode: one pass by default, restart at EOF with loop=True."""
import itertools
import time
import wave

from hpqv import demo
from hpqv.audio import FRAME_BYTES, FS

N = 5                                    # frames in the test WAV


def _wav(tmp_path):
    p = tmp_path / "in.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        for i in range(N):
            w.writeframes(bytes([i + 1]) * FRAME_BYTES)
    return str(p)


def test_looped_source_yields_more_frames_than_the_file_holds(tmp_path):
    src = demo.FrameSource("file", wav_path=_wav(tmp_path), loop=True)
    t0 = time.monotonic()
    frames = list(itertools.islice(src.frames(), 3 * N))
    src.close()
    assert len(frames) == 3 * N
    assert [f[0] for f in frames[:2 * N]] == [1, 2, 3, 4, 5] * 2    # restarts at the top
    assert time.monotonic() - t0 >= 3 * N * 0.020 * 0.9            # still paced at 20 ms


def test_unlooped_source_plays_once_and_ends(tmp_path):
    src = demo.FrameSource("file", wav_path=_wav(tmp_path))
    assert len(list(src.frames())) == N
    src.close()


def test_looping_a_wav_shorter_than_one_frame_ends_instead_of_spinning(tmp_path):
    p = tmp_path / "tiny.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(b"\0\0")
    src = demo.FrameSource("file", wav_path=str(p), loop=True)
    assert list(src.frames()) == []
    src.close()


def test_cli_loop_flag_reaches_the_frame_source(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def fake(mode, wav_path=None, seconds=30, loop=False):
        seen["loop"] = loop
        raise Stop

    monkeypatch.setattr(demo, "FrameSource", fake)
    args = demo.argparse.Namespace(input="file", wav="x.wav", seconds=1, loop=True)
    try:
        demo._run_call(None, None, None, args)
    except Stop:
        pass
    assert seen["loop"] is True


def test_call_args_carries_every_cli_default_so_code_built_calls_cannot_drift():
    # The GUI, the frozen self-check and tests build call args in code. Each new
    # CLI flag (loop, record_wav) broke one of them until it was added by hand.
    args = demo.call_args(input="file", wav="x.wav")
    assert (args.input, args.wav, args.loop, args.record_wav) == ("file", "x.wav", False, None)
    assert args.jitter_depth == 3 and args.output == "speaker"
    assert (args.key, args.cert, args.peer_cert) == (None, None, None)
    assert not hasattr(args, "identity_file")


def test_call_args_rejects_unknown_names():
    import pytest
    with pytest.raises(TypeError):
        demo.call_args(jiter_depth=3)


def test_cli_listen_and_call_complete_a_real_call(tmp_path):
    """The CLI entry points end to end: keygen x2, TCP + TLS, CALL_START, UDP voice."""
    import socket
    import threading

    for name in ("alice", "bob"):
        demo.main(["keygen", "--name", name, "--key-out", str(tmp_path / f"{name}.key"),
                   "--cert-out", str(tmp_path / f"{name}.crt")])
    wav = _wav(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def common(me, other, out):
        return ["--key", str(tmp_path / f"{me}.key"), "--cert", str(tmp_path / f"{me}.crt"),
                "--peer-cert", str(tmp_path / f"{other}.crt"), "--input", "file", "--wav", wav,
                "--output", "file", "--out-wav", str(tmp_path / out)]

    errors = []

    def run(argv):
        deadline = time.monotonic() + 10
        while True:
            try:
                return demo.main(argv)
            except ConnectionRefusedError:       # the listener is not up yet: try again
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
            except BaseException as e:
                errors.append(e)
                return

    listener = threading.Thread(target=run, args=(["listen", "--port", str(port)]
                                                  + common("bob", "alice", "bob.wav"),))
    caller = threading.Thread(target=run, args=(["call", f"127.0.0.1:{port}"]
                                                + common("alice", "bob", "alice.wav"),))
    listener.start()
    caller.start()
    caller.join(30)
    listener.join(30)
    assert not caller.is_alive() and not listener.is_alive()
    assert errors == []
    for out in ("alice.wav", "bob.wav"):
        with wave.open(str(tmp_path / out), "rb") as w:
            assert w.getnframes() > 0
