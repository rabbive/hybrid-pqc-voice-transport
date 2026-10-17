"""Task 14: hybrid handshake vs an isolated classical-only baseline (benchmark only)."""
import csv
import json
import pathlib
import socket
import subprocess
import sys

import pytest

from hpqv import identity
from hpqv.eval import classical_baseline
from hpqv.secure import HandshakeError, SecureChannel

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_handshake_bench.py"


def test_classical_rows_report_x25519_group():
    rows = classical_baseline.run(2)
    assert len(rows) == 2
    for r in rows:
        assert r["mode"] == "classical-BENCHMARK-ONLY"
        assert r["group"] == "x25519"
        assert r["handshake_ms"] > 0 and r["cpu_ms"] > 0
        assert 0 < r["handshake_bytes"] < 4000      # no 1,216 B hybrid share, no ML-DSA certs


def test_production_establish_rejects_classical_peer_wrong_group():
    """Production secure, as a call client, against the baseline server: refused, no channel.

    The plan expected reason "wrong_group". Since the T6 fix the client offers only
    X25519MLKEM768, so an x25519-only server cannot pick a group and aborts inside the
    handshake (alert handshake_failure); secure reports that as "tls_error". The
    post-handshake "wrong_group" check is only the backstop (tests/test_secure.py).
    """
    with classical_baseline.server() as (addr, result):
        sock = socket.create_connection(addr, timeout=5)
        with pytest.raises(HandshakeError) as e:
            SecureChannel.establish(sock, "client", identity.new("caller"),
                                    identity.new("listener").cert_pem, timeout=5)
    assert e.value.reason == "tls_error"
    assert "handshake failure" in str(e.value)
    assert result() == "refused"                    # the baseline side never finished either


def test_no_production_module_imports_classical_baseline():
    own = pathlib.Path(classical_baseline.__file__).resolve()
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "src" / "hpqv").rglob("*.py")
                 if p.resolve() != own and "classical_baseline" in p.read_text()]
    assert offenders == []
    # And at runtime: loading the production entry points never pulls it in.
    code = ("import sys, hpqv.secure, hpqv.demo, hpqv.call, hpqv.listener, hpqv.pairing; "
            "print('hpqv.eval.classical_baseline' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60, check=True)
    assert out.stdout.strip() == "False"


def test_bench_writes_csv_and_json_with_both_modes(tmp_path):
    subprocess.run([sys.executable, str(SCRIPT), "--n", "3", "--out", str(tmp_path)],
                   check=True, capture_output=True, timeout=120)
    with open(tmp_path / "handshake_bench.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert sorted(r["mode"] for r in rows) == ["classical-BENCHMARK-ONLY"] * 3 + ["hybrid"] * 3
    groups = {r["mode"]: r["group"] for r in rows}
    assert groups == {"hybrid": "X25519MLKEM768", "classical-BENCHMARK-ONLY": "x25519"}
    doc = json.loads((tmp_path / "handshake_bench.json").read_text())
    assert len(doc["rows"]) == 6
    assert set(doc["summary"]) == {"hybrid", "classical-BENCHMARK-ONLY"}
    assert doc["meta"]["n"] == 3 and "loopback" in doc["meta"]["network"]
    for leak in ("PRIVATE KEY", "BEGIN CERTIFICATE"):
        assert leak not in json.dumps(doc)


def test_refuses_v1_results_dir():
    out = subprocess.run([sys.executable, str(SCRIPT), "--n", "1", "--out", str(ROOT / "results")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "results/v2" in out.stderr
