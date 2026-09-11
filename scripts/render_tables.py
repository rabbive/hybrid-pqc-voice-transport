"""Generate the result tables in the docs from results/*.csv.

Numbers in prose rot. Every re-run of the sweeps shifts the measurements, and a
hand-copied table silently starts contradicting the data it claims to report —
which is exactly what happened before this script existed. So the tables are
generated, and `--check` fails the test suite if a doc has drifted out of sync
with the committed CSVs.

    python scripts/render_tables.py           # rewrite the tables
    python scripts/render_tables.py --check   # exit 1 if any doc is stale
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from hpqv.eval.stats import wilson_ci

ROOT = os.path.join(os.path.dirname(__file__), "..")
RESULTS = os.path.join(ROOT, "results")
DOCS = [os.path.join(ROOT, "docs", "EVALUATION-REPORT.md"),
        os.path.join(ROOT, "docs", "EXPERIMENT-handshake-survival.md"),
        os.path.join(ROOT, "docs", "REPORT.md")]

BEGIN = "<!-- BEGIN GENERATED: {} -->"
END = "<!-- END GENERATED: {} -->"


def _read(name):
    with open(os.path.join(RESULTS, name)) as f:
        rows = []
        for r in csv.DictReader(f):
            out = {}
            for k, v in r.items():
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    out[k] = v
            rows.append(out)
        return rows


def _index(rows, x_key):
    """-> (sorted x values, {(name, x): row})"""
    xs = sorted({r[x_key] for r in rows})
    return xs, {(r["name"], r[x_key]): r for r in rows}


def _table(header, body):
    sep = "|" + "|".join("---" for _ in header) + "|"
    return "\n".join(["| " + " | ".join(header) + " |", sep] + body)


def track2_loss():
    xs, ix = _index(_read("eval.csv"), "loss_pct")
    body = []
    for x in xs:
        h, n, t = ix[("hybrid", x)], ix[("naive", x)], ix[("tcp", x)]
        body.append("| {:.0f} % | **{:.0f}** | {:.0f} | {:.0f} | {:.2f} | {:.2f} | {:.2f} |".format(
            x, h["frag_count"], n["frag_count"], t["frag_count"],
            h["mos"], n["mos"], t["mos"]))
    return _table(["loss", "hybrid frags", "naive frags", "tcp frags",
                   "hybrid MOS", "naive MOS", "tcp MOS"], body)


def track2_latency():
    xs, ix = _index(_read("eval_latency.csv"), "delay_ms")
    body = ["| {:.0f} ms | {:.1f} ms | {:.1f} ms | {:.1f} ms |".format(
        x, ix[("hybrid", x)]["ttfb_ms"], ix[("naive", x)]["ttfb_ms"],
        ix[("tcp", x)]["ttfb_ms"]) for x in xs]
    return _table(["link delay", "hybrid TTFB", "naive TTFB", "tcp TTFB"], body)


def ns3_loss():
    xs, ix = _index(_read("ns3_eval.csv"), "loss_pct")
    body = []
    for x in xs:
        h, t, q = ix[("hybrid", x)], ix[("tcp", x)], ix[("quic", x)]
        body.append("| {:.0f} % | {:.1f} % / {:.0f} ms / {:.2f} | {:.1f} % / {:.2f} | {:.1f} % / {:.0f} ms / {:.2f} |".format(
            x, h["voice_loss_pct"], h["ttfb_ms"], h["mos"],
            t["voice_loss_pct"], t["mos"],
            q["voice_loss_pct"], q["ttfb_ms"], q["mos"]))
    return _table(["loss", "hybrid (loss / TTFB / MOS)", "tcp (loss / MOS)",
                   "quic (loss / TTFB / MOS)"], body)


def ns3_latency():
    xs, ix = _index(_read("ns3_latency.csv"), "delay_ms")
    body = ["| {:.0f} ms | {:.0f} ms | {:.0f} ms | {:.0f} ms |".format(
        x, ix[("hybrid", x)]["ttfb_ms"], ix[("tcp", x)]["ttfb_ms"],
        ix[("quic", x)]["ttfb_ms"]) for x in xs]
    return _table(["link delay", "hybrid TTFB", "tcp TTFB", "quic TTFB"], body)


def survival():
    xs, ix = _index(_read("handshake_survival.csv"), "loss_pct")
    body = []
    for x in xs:
        n, h = ix[("naive", x)], ix[("hybrid", x)]
        nlo, nhi = wilson_ci(int(n["successes"]), int(n["attempts"]))
        hlo, hhi = wilson_ci(int(h["successes"]), int(h["attempts"]))
        body.append("| {:.0f} % | **{:.0f} %** ({:.0f}–{:.0f}) | {:.0f} % | **{:.0f} %** ({:.0f}–{:.0f}) | {:.1f} ms |".format(
            x, n["success_pct"], nlo, nhi, n["predicted_pct"],
            h["success_pct"], hlo, hhi, h["median_ms"]))
    return _table(["packet loss", "naive success (95% CI)", "theory `(1-p)^8`",
                   "hybrid success (95% CI)", "hybrid median time"], body)


def fec_recovery():
    rows = sorted(_read("fec_recovery.csv"), key=lambda r: r["loss_pct"])
    body = []
    for r in rows:
        def fmt(v):
            return "99.0 dB (cap)" if v >= 99.0 else "{:.1f} dB".format(v)
        body.append("| {:.0f} % | {} | {} | {:.0f} | {:.0f} |".format(
            r["loss_pct"], fmt(r["snr_fec_on_db"]), fmt(r["snr_fec_off_db"]),
            r["frames_lost"], r["frames_recovered_by_fec"]))
    return _table(["loss", "FEC on", "FEC off", "frames lost", "recovered by FEC"], body)


TABLES = {
    "fec-recovery": fec_recovery,
    "track2-loss": track2_loss,
    "track2-latency": track2_latency,
    "ns3-loss": ns3_loss,
    "ns3-latency": ns3_latency,
    "survival": survival,
}


def _substitute(text, name, table):
    begin, end = BEGIN.format(name), END.format(name)
    if begin not in text:
        return text, False
    head, rest = text.split(begin, 1)
    _old, tail = rest.split(end, 1)
    return head + begin + "\n" + table + "\n" + end + tail, True


def render(check=False):
    tables = {name: fn() for name, fn in TABLES.items()}
    stale = []
    for doc in DOCS:
        original = open(doc).read()
        text = original
        for name, table in tables.items():
            text, _found = _substitute(text, name, table)
        if text != original:
            if check:
                stale.append(os.path.relpath(doc, ROOT))
            else:
                open(doc, "w").write(text)
                print("updated", os.path.relpath(doc, ROOT))
    return stale


if __name__ == "__main__":
    if "--check" in sys.argv:
        stale = render(check=True)
        if stale:
            print("STALE (run scripts/render_tables.py): " + ", ".join(stale))
            sys.exit(1)
        print("docs match results/")
    else:
        render()
