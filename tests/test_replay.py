"""Sliding 256-packet anti-replay window (RFC 4303 section 3.4.3 style).

The window covers [top - 255, top], where top is the highest accepted seq.
A seq exactly 256 behind top is outside the window and rejected.
"""
import random
import time

import pytest

from hpqv.packet import MAX_SEQ
from hpqv.replay import ReplayWindow


def _take(w, seq):
    ok = w.check(seq)
    if ok:
        w.accept(seq)
    return ok


def test_fresh_window_accepts_seq_zero_once():
    w = ReplayWindow()
    assert w.check(0)
    w.accept(0)
    assert not w.check(0)


def test_check_is_pure():
    w = ReplayWindow()
    assert w.check(5) and w.check(5)
    w.accept(5)
    assert w.check(4) and w.check(4)
    assert w.check(6) and w.check(6)


def test_reordering_within_window_accepted_once_duplicates_rejected():
    w = ReplayWindow()
    base = 1000
    block = list(range(base, base + 200))
    random.Random(4).shuffle(block)
    assert all(_take(w, s) for s in block)
    dupes = block[:]
    random.Random(5).shuffle(dupes)
    assert not any(_take(w, s) for s in dupes)


def test_window_boundary_255_inside_256_outside():
    w = ReplayWindow()
    w.accept(1000)
    assert w.check(1000 - 255)
    assert not w.check(1000 - 256)
    assert not w.check(0)
    w.accept(1000 - 255)
    assert not w.check(1000 - 255)


def test_marks_shift_with_top():
    w = ReplayWindow()
    w.accept(10)
    w.accept(12)                   # 11 still missing
    assert w.check(11) and not w.check(10)
    w.accept(10 + 255)             # 10 is now exactly 255 behind top
    assert not w.check(10) and w.check(11) and not w.check(12)
    w.accept(12 + 255)             # 10, 11 drop out of the window; 12 at the edge
    assert not w.check(11) and not w.check(12)


def test_large_forward_jump_is_bounded_and_resets_marks():
    w = ReplayWindow()
    w.accept(0)
    start = time.perf_counter()
    top = 0
    for _ in range(1000):
        top += 2**37
        w.accept(top)
    assert time.perf_counter() - start < 1.0
    assert not w.check(top) and w.check(top - 1) and w.check(top - 255)
    assert not w.check(top - 256) and not w.check(0)


def test_seq_range_edges():
    w = ReplayWindow()
    assert not w.check(-1)
    assert not w.check(MAX_SEQ + 1)
    assert w.check(MAX_SEQ)
    w.accept(MAX_SEQ)
    assert not w.check(MAX_SEQ)
    assert w.check(MAX_SEQ - 1) and not w.check(MAX_SEQ - 256)


def test_accept_refuses_what_check_rejects():
    w = ReplayWindow()
    w.accept(300)
    for bad in (300, 44, -1, MAX_SEQ + 1):
        with pytest.raises(ValueError):
            w.accept(bad)
    assert w.check(299)


def test_custom_size():
    w = ReplayWindow(size=4)
    w.accept(10)
    assert w.check(7) and not w.check(6)
    with pytest.raises(ValueError):
        ReplayWindow(size=0)
