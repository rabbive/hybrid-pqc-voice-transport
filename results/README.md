# Results

Generated evaluation data and figures. These are **committed deliberately** so the
report and defense can cite exact numbers, and so the figures are reviewable without
re-running the harness.

| File | Produced by | Contents |
|---|---|---|
| `eval.csv` | `scripts/run_eval.py` | Track 2 loss sweep (5-run medians): fragment count, drop rate, jitter, TTFB, MOS |
| `eval_latency.csv` | `scripts/run_eval.py` | Track 2 latency sweep for TTFB |
| `ns3_eval.csv` | `scripts/run_ns3.py` | Track 3 NS-3 loss sweep (hybrid / tcp / quic) |
| `ns3_latency.csv` | `scripts/run_ns3.py` | Track 3 NS-3 latency sweep |
| `handshake_survival.csv` | `scripts/run_handshake_survival.py` | Handshake completion rate vs loss, 100 attempts/cell |
| `*.png` | the same scripts | The figures for the report |

Regenerate everything with the commands in `docs/EVALUATION-REPORT.md` §7.
Numbers vary slightly between runs (network measurement is sampled, not deterministic) —
the committed CSVs are the specific run the report quotes.
