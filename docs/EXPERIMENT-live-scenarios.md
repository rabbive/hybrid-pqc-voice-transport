# Live pipeline scenarios

## Purpose

This experiment measures loss repair and jitter-buffer behavior in real loopback calls. It records WAV output, packet counters, Telemetry MOS estimates, aligned whole-signal SNR, and repaired-frame SNR against a clean live decode.

## Method

Each scenario starts a caller and listener over loopback TCP and UDP. The peers complete the normal handshake, create per-call keyrings, encode and decode with Opus, pass datagrams through the impairment controls, repair gaps in the jitter buffer, and write received audio to WAV.

The caller's sender applies the scenario's loss, delay and jitter. The listener applies the repair mode and jitter-buffer depth, and every reported number except `frames_offered` is measured by the listener on the stream it receives. The return direction stays clean and is not reported.

The script runs each condition three times for 10 seconds (500 frames of 20 ms). It assigns seeds 20261015, 20261016 and 20261017 to the three runs. The sender's `LossModel` uses the run seed, its `DelayLine` uses the next integer, and the listener's incoming `LossModel` uses the run seed plus 10. The cryptographic handshake uses the system random source. Because the drop decision depends only on the seed, the silence, PLC and FEC runs at one seed drop exactly the same frames. The comparison between repair modes is paired.

When you omit `--wav`, the script creates a deterministic 10-second, 48 kHz, mono, 16-bit PCM signal. The signal combines harmonics with a 4.3 Hz syllable-rate amplitude envelope. It is speech-like, not recorded speech.

### Counters

The definitions match `hpqv.telemetry`:

- Expected: the span of sequence numbers the listener received. The sender advances the sequence on frames it drops, so this counts every frame its encoder produced. A frame dropped at the very end of the call is not counted, because no later packet reveals it. That is why some loss runs expect 499 frames. `frames_offered` in the CSV is the sender's own count and is 500 in every run.
- Delivered: received minus dropped on receipt.
- Lost: expected minus delivered, plus late. A late packet arrived after its slot was played, so the listener lost it too.
- FEC-recovered: frames rebuilt from LBRR data that the next packet actually carried, checked with libopus `opus_packet_has_lbrr`. Concealed: frames filled by PLC or silence.
- Network loss % = (expected − delivered) / expected. Residual loss % = (lost − FEC-recovered) / expected. That is the share of frames never played as real audio.
- Rx jitter is the RFC 3550 interarrival jitter estimate. Added delay is the mean one-way transit above the lowest transit seen in the call. The base 100 ms delay is present from the first packet, so it is not part of this figure.

### Audio scores

The clean reference is a separate live loopback decode of the same source with the same FEC and bitrate settings and no impairment. The clean row is scored against this independent call, not against itself. The reference is regenerated on every sweep.

The scorer searches frame offsets from −25 through +25 and scores only the overlapping span. A positive offset means the received stream starts later than the reference.

Whole-signal SNR is `10 log10(Σ reference² / Σ (reference − output)²)` over the aligned span. Repaired-frame SNR uses the same formula only on the frames the listener filled after a sequence gap. Both are uncapped. An exact match is +∞. A row with no repaired frames has no repaired-frame SNR.

Waveform SNR penalises phase-misaligned concealment and is not a perceptual score. Silence fills a missing frame with zeros, so on repaired frames it scores exactly 0 dB (noise = reference). PLC extrapolates a plausible waveform, but the phase is usually off, and the difference from the reference can carry more energy than the reference itself. That gives a score below 0 dB. So PLC can sound better than silence and still score lower here.

### MOS

Telemetry samples once per second. Each sample's MOS is the project's ITU-T G.107 E-model estimate (`hpqv.eval.mos`) from that interval's residual loss and an effective delay of 20 ms × jitter-buffer depth plus the added delay. The reported MOS is the mean of the samples weighted by frames expected per interval, so the short tail interval at hang-up cannot swing it. The 100 ms base delay is not in the delay term (see added delay above).

### Aggregation

Each row in `results/live_scenarios.csv` is the mean over the three seeds. Every condition uses the same three seeds, so means keep the paired comparison. The mean counts still add up: lost − recovered = residual. Loss percentages are pooled: total residual frames over total expected frames. Every individual run is in `results/live_scenarios_runs.csv`. The WAV kept for each condition is the seed-20261016 run.

### FEC coverage

FEC can only rebuild a lost frame when the next packet carries LBRR. Opus decides per frame whether to include LBRR. With the sender's FEC settings (in-band FEC on, expected loss 10 %), 212 of the 500 encoded packets of the synthetic source carry LBRR (42 %). The script prints this count at the start of each sweep. A burst of three lost frames can recover at most its last frame. That caps recovery well below the loss rate on this signal.

## Results

Run on 2026-10-16, macOS, loopback. Means over seeds 20261015–20261017; three 10-second calls per row.

| Scenario | Expected | Lost | Late | FEC-recovered | Concealed | Network loss % | Residual loss % | Rx jitter (ms) | Added delay (ms) | MOS | Whole SNR (dB) | Repaired-frame SNR (dB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| clean | 500.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.00 | 0.00 | 1.9 | 3.8 | 4.36 | ∞ | — |
| random-10-silence | 499.7 | 49.0 | 0.0 | 0.0 | 49.0 | 9.81 | 9.81 | 1.6 | 3.5 | 3.13 | 9.07 | 0.00 |
| random-10-plc | 499.7 | 49.0 | 0.0 | 0.0 | 49.0 | 9.81 | 9.81 | 1.3 | 3.0 | 3.13 | 7.87 | −0.79 |
| random-10-fec | 499.7 | 49.0 | 0.0 | 15.3 | 33.7 | 9.81 | 6.74 | 1.3 | 3.0 | 3.48 | 14.03 | 4.00 |
| random-20-silence | 499.3 | 94.7 | 0.0 | 0.0 | 94.7 | 18.96 | 18.96 | 1.4 | 2.9 | 2.39 | 5.91 | 0.00 |
| random-20-plc | 499.3 | 94.7 | 0.0 | 0.0 | 94.7 | 18.96 | 18.96 | 1.3 | 3.0 | 2.39 | 4.63 | −0.91 |
| random-20-fec | 499.3 | 94.7 | 0.0 | 29.7 | 65.0 | 18.96 | 13.02 | 2.0 | 2.9 | 2.83 | 10.64 | 3.80 |
| burst-10-plc (B=3) | 500.0 | 53.7 | 0.0 | 0.0 | 53.7 | 10.73 | 10.73 | 1.8 | 3.0 | 3.13 | 7.72 | −1.70 |
| burst-10-fec (B=3) | 500.0 | 53.7 | 0.0 | 6.3 | 47.3 | 10.73 | 9.47 | 1.3 | 3.0 | 3.26 | 9.10 | −1.13 |
| delay 100±30 ms, depth 2 | 500.0 | 16.7 | 16.7 | 6.7 | 10.0 | 0.00 | 2.00 | 24.3 | 32.4 | 4.06 | 21.88 | 7.94 |
| delay 100±30 ms, depth 6 | 500.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.00 | 0.00 | 23.5 | 31.6 | 4.26 | ∞ | — |

The delay rows use FEC repair. Charts: `results/live_scenarios_residual_loss.png`, `results/live_scenarios_snr.png` (∞ bars are drawn at a fixed height and marked ∞), `results/live_scenarios_mos.png`, `results/live_scenarios_late_by_depth.png`.

Paired view of the loss scenarios, per seed (residual loss %; repaired-frame SNR in dB in brackets):

| Scenario | Seed | Network loss % | Silence | PLC | FEC |
|---|---|---|---|---|---|
| random 10 % | 20261015 | 11.20 | 11.20 (0.00) | 11.20 (−1.05) | 6.40 (6.57) |
| random 10 % | 20261016 | 9.80 | 9.80 (0.00) | 9.80 (0.26) | 8.00 (2.21) |
| random 10 % | 20261017 | 8.42 | 8.42 (0.00) | 8.42 (−1.57) | 5.81 (3.20) |
| random 20 % | 20261015 | 22.20 | 22.20 (0.00) | 22.20 (−0.86) | 15.20 (2.25) |
| random 20 % | 20261016 | 18.44 | 18.44 (0.00) | 18.44 (−0.52) | 13.63 (4.07) |
| random 20 % | 20261017 | 16.23 | 16.23 (0.00) | 16.23 (−1.35) | 10.22 (5.07) |
| burst 10 %, B=3 | 20261015 | 11.80 | — | 11.80 (−1.63) | 9.80 (−1.94) |
| burst 10 %, B=3 | 20261016 | 11.00 | — | 11.00 (−0.98) | 9.80 (−0.27) |
| burst 10 %, B=3 | 20261017 | 9.40 | — | 9.40 (−2.48) | 8.80 (−1.17) |

### Sanity checks

- Zero-loss calls decode bit-exactly over the aligned span. The clean and depth-6 delay rows score +∞ whole-signal SNR against an independent clean call in all six runs. The test suite asserts ≥ 60 dB.
- On repaired frames, silence scores exactly 0.00 dB in every run, as zero-fill must. If the mapping from sequence numbers to output frames were off by even one frame, these would not be exactly zero.
- At the same seed, FEC's repaired-frame SNR is above silence's in all six random-loss runs. The test suite asserts this at 20 % loss.

## What these results support

- The live call path applies scripted loss and delay, and the listener reports consistent expected, delivered, late, concealed and FEC-recovered counts. Silence, PLC and FEC see identical drops at each seed.
- In these runs, FEC lowered residual loss compared with silence or PLC at every seed. Under random loss it went from 9.81 % to 6.74 % at 10 % and from 18.96 % to 13.02 % at 20 %. Under 10 % burst loss with B=3, PLC's 10.73 % fell to 9.47 % with FEC.
- FEC recovered about a third of lost frames under random loss (15.3/49 and 29.7/94.7) and about an eighth under burst loss (6.3/53.7). The limits are LBRR coverage (42 % of packets on this signal) and the fact that only the last frame of a burst can be rebuilt.
- With FEC, whole-signal SNR was higher than with silence or PLC at every random-loss seed, and higher than with PLC at every burst-loss seed.
- At 100 ms ± 30 ms delay, a depth-2 jitter buffer saw 16.7 late packets per 500 frames (2.00 % residual after FEC). Depth 6 saw none and decoded bit-exactly. The deeper buffer still had the higher MOS estimate (4.26 vs 4.06): in the E-model, 2 % residual loss costs more than the extra 80 ms of buffering.
- PLC and silence leave the same residual loss, because neither restores the lost audio. On repaired frames PLC scored below silence's 0 dB in 5 of 6 random-loss runs and below 0 dB in all 3 burst runs. This is the phase effect described under Audio scores, not evidence that PLC sounds worse.

## What these results do not support

- Loopback does not model a real network, radio link, route or remote host. The delay rows' jitter comes from scheduling a timer thread, so those rows vary run to run. The loss rows reproduced exactly across repeated sweeps, given the seeds.
- MOS is the E-model estimate from `hpqv.eval.mos`, not a listening test or user rating. It leaves out the 100 ms base delay, so absolute MOS values in the delay rows are optimistic.
- Whole-signal and repaired-frame SNR compare received output with a clean Opus decode. They do not measure intelligibility or preference, and they penalise phase-shifted concealment that a listener may not notice.
- The source is synthetic. LBRR coverage, and so FEC recovery, depends on the signal: Opus gates LBRR on its voice-activity decision, so recorded speech may give different coverage.
- Three seeds per condition show these conditions only. They do not establish behavior across loss rates, burst lengths, devices or speakers, and they are too few for confidence intervals.
- File output never skips frames. The speaker-mode latency bound (`stats.skipped`) is not exercised here.

## Reproduce

Run the full matrix:

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src .venv/bin/python scripts/run_live_scenarios.py
```

Use `--quick` for one 4-second run per condition, or pass `--wav path/to/input.wav` for a 48 kHz mono 16-bit PCM WAV.

The full sweep makes 33 loopback calls plus two clean reference calls and takes about 8 minutes. It writes `results/live_scenarios.csv` (means), `results/live_scenarios_runs.csv` (every run), four PNG charts under `results/`, and one received WAV per condition plus the clean references under `results/live_scenarios/`.
