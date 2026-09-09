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
