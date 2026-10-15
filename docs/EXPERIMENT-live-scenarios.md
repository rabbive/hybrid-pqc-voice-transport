# Live pipeline scenarios

## Purpose

This experiment measures loss repair and jitter-buffer behavior in real loopback calls. It records WAV output, packet counters, Telemetry MOS estimates, and segmental SNR against a clean live decode.

## Method

Each scenario starts a caller and listener over loopback TCP and UDP. The peers complete the normal handshake, create per-call keyrings, encode and decode with Opus, pass datagrams through the impairment controls, repair gaps in the jitter buffer, and write received audio to WAV.

The caller sends the measured stream. The listener applies the selected repair mode and jitter-buffer depth. The return direction stays clean. Its audio is not included in the reported counters or WAV.

The script runs each condition three times for 10 seconds and reports medians. It assigns seeds 20261015, 20261016, and 20261017 to the three runs. The sender's `LossModel` uses the run seed, its `DelayLine` uses the next integer, and the listener's incoming `LossModel` uses the run seed plus 10. The cryptographic handshake uses the system random source.

When you omit `--wav`, the script creates a deterministic 10-second, 48 kHz, mono, 16-bit PCM signal. The signal combines harmonics with a 4.3 Hz syllable-rate amplitude envelope. It is speech-like, not recorded speech.

The clean reference is a separate live loopback decode of the same source WAV and duration. The SNR calculation aligns the streams at 20 ms frame boundaries and reuses `fec_recovery._segmental_snr_db`. The metric compares decoded audio with that clean decode, not with the source PCM.

Telemetry samples once per second. Measured loss is the difference between frames offered by the sender and received by the listener. Residual loss also counts late frames, then subtracts FEC-recovered frames. MOS uses the project's E-model estimate with residual loss, configured jitter, and effective delay.

## Results

Each row is the median of three 10-second calls. Each run offered 500 frames. The range column shows the minimum and maximum residual-loss percentage across those runs.

| Scenario | Measured loss | Residual loss, median (range) | FEC recovered | Mean MOS | Segmental SNR, median (range) | Late |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clean | 0.0% | 0.0% (0.0–0.0) | 0 | 4.36 | 99.00 dB (99.00–99.00) | 0 |
| Random 10%, silence | 9.8% | 9.8% (8.6–11.2) | 0 | 3.13 | 50.53 dB (40.51–53.04) | 0 |
| Random 10%, PLC | 9.8% | 9.8% (8.6–11.2) | 0 | 3.13 | 49.80 dB (37.87–50.74) | 0 |
| Random 10%, FEC | 9.8% | 1.2% (1.0–1.4) | 42 | 4.15 | 7.99 dB (7.81–8.14) | 0 |
| Random 20%, silence | 18.6% | 18.6% (16.4–22.2) | 0 | 2.41 | 22.15 dB (-2.25–32.91) | 0 |
| Random 20%, PLC | 18.6% | 18.6% (16.4–22.2) | 0 | 2.41 | 19.58 dB (-2.28–28.72) | 0 |
| Random 20%, FEC | 18.6% | 3.8% (2.8–4.4) | 74 | 3.78 | 6.69 dB (-1.86–7.48) | 0 |
| Burst 10%, B=3, PLC | 11.0% | 11.0% (9.4–11.8) | 0 | 3.07 | 58.86 dB (57.97–69.40) | 0 |
| Burst 10%, B=3, FEC | 11.0% | 7.4% (6.4–8.0) | 18 | 3.47 | 7.88 dB (7.71–7.91) | 0 |
| Delay 100 ± 30 ms, depth 2, FEC | 0.0% | 0.0% (0.0–0.0) | 17 | 4.20 | 8.79 dB (8.76–8.82) | 17 |
| Delay 100 ± 30 ms, depth 6, FEC | 0.0% | 0.0% (0.0–0.0) | 0 | 3.79 | 9.10 dB (9.10–9.10) | 0 |

FEC reduced median residual loss in all tested loss conditions. The deeper jitter buffer eliminated late packets in the delay condition, with a lower MOS estimate because the effective delay includes the buffer depth.

FEC cases have much lower waveform SNR than the clean decode even when residual loss falls. Treat the SNR as a waveform-difference measure, not a perceptual score. The runs use a harmonic synthetic signal, and the metric is sensitive to decoder history and frame alignment.

## What these results support

- The live call path applies scripted loss and delay, records listener output, and reports the expected offered, received, late, concealed, and FEC-recovered counts.
- In these seeded loopback runs, FEC reduces residual loss compared with silence or PLC under random 10% and 20% loss, and under burst 10% loss.
- At 100 ms ± 30 ms delay, depth 2 records late packets while depth 6 records none. The E-model MOS estimate falls as buffer depth raises effective delay.

## What these results do not support

- Loopback does not model a real network, radio link, route, or remote host.
- MOS is the E-model estimate from `hpqv.eval.mos`, not a listening test or a user rating.
- Segmental SNR compares received output with a clean Opus decode. It does not measure intelligibility or preference.
- The generated signal is synthetic and does not establish performance on recorded speech or multiple speakers.
- Three seeded runs show only the listed conditions and seeds. They do not establish behavior across all loss patterns or devices.

## Reproduce

Run the full matrix:

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src .venv/bin/python scripts/run_live_scenarios.py
```

Use `--quick` for one 4-second run per condition, or pass `--wav path/to/input.wav` for a 48 kHz mono 16-bit PCM WAV.

The script writes `results/live_scenarios.csv`, four PNG charts under `results/`, and one received WAV per condition under `results/live_scenarios/`.
