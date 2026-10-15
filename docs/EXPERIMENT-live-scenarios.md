# Live pipeline scenarios

## Purpose

This experiment measures loss repair and jitter-buffer behavior in real loopback calls. It records WAV output, packet counters, Telemetry MOS estimates, aligned whole-signal SNR, and repaired-frame SNR against a clean live decode.

## Method

Each scenario starts a caller and listener over loopback TCP and UDP. The peers complete the normal handshake, create per-call keyrings, encode and decode with Opus, pass datagrams through the impairment controls, repair gaps in the jitter buffer, and write received audio to WAV.

The caller sends the measured stream. The listener applies the selected repair mode and jitter-buffer depth. The return direction stays clean. Its audio is not included in the reported counters or WAV.

The script runs each condition three times for 10 seconds and reports medians. It assigns seeds 20261015, 20261016, and 20261017 to the three runs. The sender's `LossModel` uses the run seed, its `DelayLine` uses the next integer, and the listener's incoming `LossModel` uses the run seed plus 10. The cryptographic handshake uses the system random source.

When you omit `--wav`, the script creates a deterministic 10-second, 48 kHz, mono, 16-bit PCM signal. The signal combines harmonics with a 4.3 Hz syllable-rate amplitude envelope. It is speech-like, not recorded speech.

The clean reference is a separate live loopback decode of the same source WAV and duration, with the same FEC and bitrate settings. The scorer searches frame offsets from -25 through +25 and scores only the overlapping span. A positive offset means the received stream starts later than the clean reference. The CSV records the chosen offset.

Whole-signal SNR is `10 log10(sum(reference²) / sum((reference - output)²))` over the aligned span. Repaired-frame SNR uses the same formula only on frames that the receiver had to repair after a sequence gap. Both metrics are uncapped. A perfect match is positive infinity. A row with no repaired frames has no repaired-frame SNR.

Telemetry samples once per second. Measured loss is the difference between frames offered by the sender and received by the listener. Residual loss also counts late frames, then subtracts FEC-recovered frames. MOS uses the project's E-model estimate with residual loss, configured jitter, and effective delay.

## Results

The previous full-sweep table used capped segmental SNR and pre-F1 telemetry. Do not cite those SNR values or use those residual-loss and MOS values as the final report. The full table and charts will be regenerated after the receiver-side telemetry merge.

A 2-second, seed-42, 20% random-loss check offered 100 frames in each repair run. It measured repaired-frame SNR of 0.43 dB for FEC, -1.63 dB for PLC, and 0.00 dB for silence. FEC beat silence. PLC scored below silence on this synthetic waveform. Waveform SNR penalises phase-misaligned concealment; it is not a perceptual score. A zero-loss depth-6 delay call had no late frames and infinite whole-signal SNR against the FEC-enabled clean reference.

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
