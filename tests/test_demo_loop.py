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
        demo._run_call(None, None, None, None, args)
    except Stop:
        pass
    assert seen["loop"] is True
