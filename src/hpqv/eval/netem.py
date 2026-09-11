import subprocess
from contextlib import contextmanager

@contextmanager
def netem(loss_pct=0.0, delay_ms=0.0, jitter_ms=0.0, dev="lo"):
    parts = ["tc","qdisc","add","dev",dev,"root","netem"]
    if delay_ms: parts += ["delay", f"{delay_ms}ms"] + ([f"{jitter_ms}ms"] if jitter_ms else [])
    if loss_pct: parts += ["loss", f"{loss_pct}%"]
    subprocess.run(parts, check=True)
    try:
        yield
    finally:
        subprocess.run(["tc","qdisc","del","dev",dev,"root"],
                       stderr=subprocess.DEVNULL)


@contextmanager
def loopback_mtu(mtu: int = 1500, dev: str = "lo", restore: int = 65536):
    """Pin an interface's MTU for the duration of a measurement.

    Docker's loopback defaults to a 65536 MTU, under which nothing ever
    fragments. Any experiment that depends on fragmentation must pin this to a
    realistic 1500 first, or it silently measures nothing.
    """
    subprocess.run(["ip", "link", "set", dev, "mtu", str(mtu)], check=True)
    try:
        yield
    finally:
        subprocess.run(["ip", "link", "set", dev, "mtu", str(restore)],
                       stderr=subprocess.DEVNULL)
