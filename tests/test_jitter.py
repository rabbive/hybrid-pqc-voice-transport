from hpqv.jitter import JitterBuffer

def test_reorders_and_drops_late():
    jb = JitterBuffer(depth=2)
    jb.push(2, b"b"); jb.push(1, b"a")   # arrive out of order
    assert jb.pop() == b"a"              # buffer full -> emit lowest
    jb.push(3, b"c")
    assert jb.pop() == b"b"
    jb.push(1, b"late")                  # older than last popped -> dropped
    jb.push(4, b"d")
    assert jb.pop() == b"c"
