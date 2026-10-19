"""Hybrid (X25519MLKEM768 + ML-DSA-65) vs classical-only (x25519 + Ed25519) TLS 1.3
handshake on loopback, no impairment. Writes handshake_bench.csv, .json and .png.

    python scripts/run_handshake_bench.py --n 50 --out results/v2
"""
import argparse
import csv
import datetime
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from unittest import mock

import cryptography
import OpenSSL
from cryptography.hazmat.backends.openssl import backend

from hpqv import identity, secure
from hpqv.eval import classical_baseline, graphs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["mode", "handshake_ms", "handshake_bytes", "cpu_ms", "group"]
METRICS = ["handshake_ms", "handshake_bytes", "cpu_ms"]
DEFINITIONS = {
    "handshake_ms": "client wall clock inside establish(): TLS context build, full TLS 1.3 "
                    "mutual-auth handshake, until the server's 3-byte READY record arrives",
    "handshake_bytes": "TCP payload bytes the client sent plus received over that span "
                       "(TLS records only; no TCP/IP headers)",
    "cpu_ms": "process CPU time (user+sys), both peers in one process, over one local pair: "
              "socket setup, both handshakes, thread join; identity generation excluded",
    "group": "key exchange group OpenSSL reports as negotiated",
}


def hybrid_once() -> dict:
    caller, listener = identity.new("caller"), identity.new("listener")
    # local_pair() makes its own identities; hand it these so keygen stays out of cpu_ms.
    with mock.patch.object(identity, "new", side_effect=[caller, listener]):
        cpu0 = time.process_time()
        client, server = secure.local_pair()
        cpu_ms = (time.process_time() - cpu0) * 1000
    row = {"mode": "hybrid", "handshake_ms": client.handshake_ms,
           "handshake_bytes": client.handshake_bytes, "cpu_ms": cpu_ms, "group": client.group}
    client.close()
    server.close()
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for mode in dict.fromkeys(r["mode"] for r in rows):
        out[mode] = {}
        for m in METRICS:
            v = sorted(r[m] for r in rows if r["mode"] == mode)
            q = statistics.quantiles(v, n=4) if len(v) > 1 else [v[0]] * 3
            out[mode][m] = {"median": statistics.median(v), "p25": q[0], "p75": q[2],
                            "min": v[0], "max": v[-1], "mean": statistics.fmean(v)}
    return out


def _cmd(*args) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10,
                              cwd=ROOT).stdout.strip()
    except OSError:
        return ""


def meta(n: int) -> dict:
    return {
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(["python", "scripts/run_handshake_bench.py", *sys.argv[1:]]),
        "git_commit": _cmd("git", "rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_cmd("git", "status", "--porcelain")),
        "n": n,
        "network": "loopback 127.0.0.1 TCP, no impairment, no netem/proxy",
        "order": "interleaved: hybrid row i, then classical row i, for i in 1..n, so load "
                 "drift hits both modes alike. Hybrid rows run in this process after one "
                 "discarded warm-up; each classical row runs in a fresh child process after "
                 "one discarded warm-up in that child",
        "load_average_1_5_15": {"before": os.getloadavg()},
        "machine": {"cpu": _cmd("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
                    "cores": os.cpu_count(), "macos": platform.mac_ver()[0],
                    "arch": platform.machine(),
                    "platform": platform.platform()},
        "software": {"python": platform.python_version(), "openssl": backend.openssl_version_text(),
                     "pyopenssl": OpenSSL.__version__, "cryptography": cryptography.__version__},
        "modes": {"hybrid": "production secure.local_pair(): X25519MLKEM768, ML-DSA-65 certs",
                  classical_baseline.MODE: "hpqv.eval.classical_baseline in a child process, "
                                           "OPENSSL_CONF Groups = x25519, Ed25519 certs. "
                                           "BENCHMARK ONLY, NOT SECURE AGAINST QUANTUM ADVERSARIES"},
        "definitions": DEFINITIONS,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--out", default="results/v2")
    a = p.parse_args(argv)
    out = os.path.abspath(a.out)
    v1, v2 = os.path.join(ROOT, "results"), os.path.join(ROOT, "results", "v2")
    if os.path.commonpath([out, v1]) == v1 and os.path.commonpath([out, v2]) != v2:
        p.error("v1 results/ is never modified; write under results/v2")
    if not 1 <= a.n <= classical_baseline.MAX_N:
        p.error(f"--n must be 1..{classical_baseline.MAX_N}")

    info = meta(a.n)                # before any output exists, so git_dirty means source
    hybrid_once()                   # warm-up, discarded (classical_baseline.run does its own)
    rows = []
    for _ in range(a.n):
        rows += [hybrid_once(), *classical_baseline.run(1)]
    for r in rows:
        r["handshake_ms"], r["cpu_ms"] = round(r["handshake_ms"], 3), round(r["cpu_ms"], 3)
    info["load_average_1_5_15"]["after"] = os.getloadavg()
    summary = summarize(rows)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "handshake_bench.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(out, "handshake_bench.json"), "w") as f:
        json.dump({"meta": info, "summary": summary, "rows": rows}, f, indent=2)
        f.write("\n")
    graphs.plot_handshake_bench(summary, os.path.join(out, "handshake_bench.png"), {
        "hybrid": "Hybrid PQ\nX25519MLKEM768 + ML-DSA-65",
        "classical-BENCHMARK-ONLY": "Classical baseline\nx25519 + Ed25519"})
    for mode, s in summary.items():
        print(f"{mode:>24}: median {s['handshake_ms']['median']:.2f} ms, "
              f"{s['handshake_bytes']['median']:.0f} B, cpu {s['cpu_ms']['median']:.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
