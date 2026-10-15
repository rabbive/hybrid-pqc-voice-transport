# Live demo

Real two-peer voice call over the actual hpqv stack: post-quantum handshake over
TCP, then Opus-encoded voice over UDP, both directions. The GUI changes network
impairment, repair mode, buffering, bitrate and keys while the call runs (works on
macOS, unlike `tc netem`).

**Presenting it?** Follow the minute-by-minute script in
**[DEMO-RUNBOOK.md](DEMO-RUNBOOK.md)**. This page covers running the call.

## GUI (recommended for the defense)

```bash
python -m hpqv.gui
```

A single window over the same call machinery as the CLI — same handshake, same
transport, so you are demonstrating exactly what was measured.

1. Pick the shared `identity.json` (the **…** button), or **Create identity** once
   and copy the file privately to the other computer.
2. Choose **Listen on port** on one machine, **Call** with `host:port` on the other.
   Input is *Microphone* or a *WAV file* (48 kHz mono 16-bit; it plays once and then
   the call ends on both sides). Tick *Record received audio* before Start to save
   what you hear.
3. **Start.** The state line moves: listening → handshake → *connected — talk*. The
   **Call** tab's handshake panel shows the algorithms, message sizes, TCP transport,
   measured handshake time and the largest datagram against the 1200 B cap.
4. Talk. The counters climb.
5. **Change things mid-call — no hang-up.** The **Network** tab has presets
   (*Clean*, *Bad Wi-Fi*, *Burst loss*, *Congested*), repair mode (*Silence* /
   *Concealment only* / *FEC + concealment*), outgoing and incoming loss, random or
   burst loss model, delay, jitter, jitter-buffer depth, Opus bitrate and an *Encoder
   FEC* checkbox. **Security → Rotate keys** re-keys both sides and shows a new
   fingerprint.
6. **Read the tiles correctly.** Outgoing controls change what the *peer* hears; the
   tiles, graph and MOS show the audio *you* receive. Incoming loss, repair mode and
   jitter depth act on your own receiver. MOS is an E-model estimate, not a listening
   test.
7. **Hang up** ends the call on both sides. **Export session…** (also after hang-up)
   writes `timeline.csv`, `summary.md` and three charts.

Requires no extra dependencies — tkinter ships with Python.

## 1. Generate a shared identity file

Both peers need a shared identity file so each side can pin the other's public key.
Generate it once, then copy it to both machines (e.g. via `scp`, AirDrop, USB):

```bash
python -m hpqv.demo keygen --out identity.json
```

This file contains keypairs for both roles (`listener` and `caller`). Treat it like
a shared secret for the demo — don't publish it.

## 2. Start the listener (machine A)

```bash
python -m hpqv.demo listen --port 9000 --identity-file identity.json
```

Prints its fingerprint and waits for a call. Mic input / speaker output are the
defaults.

## 3. Call from the caller (machine B)

Same LAN:

```bash
python -m hpqv.demo call 192.168.1.42:9000 --identity-file identity.json
```

Same machine (localhost, e.g. rehearsing solo with two terminals):

```bash
python -m hpqv.demo listen --port 9000 --identity-file identity.json
python -m hpqv.demo call 127.0.0.1:9000 --identity-file identity.json
```

Once the handshake completes both sides print a live status line:

```
[status] sent=120 received=118 dropped=0 recovered=0 drop_pct=0%
```

## 4. Packet loss from the CLI

Loss is applied on the **sending** side, dropping outgoing datagrams before they hit
the wire. In the CLI the value is fixed for the run, so restart the `call` side with
`--drop-pct`:

```bash
python -m hpqv.demo call 192.168.1.42:9000 --identity-file identity.json --drop-pct 30
```

The CLI has no live knobs. For a live presentation use the GUI, where loss, repair
mode, delay, jitter and the rest change mid-call (see the runbook).

## What the audience should hear

- At `--drop-pct 0`: clean, low-latency two-way audio.
- At `--drop-pct 20-30`: audible but stable audio — occasional soft glitches, no
  crash, `dropped` climbing on the sending side while `received` on the far end
  falls behind by the same fraction, and `recovered` counting frames the receiver
  rebuilt with Opus in-band FEC (the rest are concealed). Loss here is independent
  random loss; bursts defeat FEC.
- At `--drop-pct 60+`: clearly degraded but the call keeps running — the transport
  does not crash or desync under stress.

## File-only mode (no mic/speaker, e.g. CI or a headless box)

```bash
python -m hpqv.demo listen --port 9000 --identity-file identity.json \
  --input file --wav in.wav --output file --out-wav out.wav --seconds 10

python -m hpqv.demo call 127.0.0.1:9000 --identity-file identity.json \
  --input file --wav in.wav --output file --out-wav out.wav --seconds 10
```

`--wav` must be 48kHz mono 16-bit PCM and plays once, then the call ends on both sides.
Both `--input` and `--output` can be
mixed independently (e.g. `--input mic --output file` to record what the far
end would hear).

## Troubleshooting

**macOS asks for microphone permission the first time.** Grant it in
System Settings → Privacy & Security → Microphone for the terminal app you're
running from (Terminal.app, iTerm, etc.), then re-run.

**`RuntimeError: no microphone available` / `no speaker available`.** No audio
device is attached (headless machine, container, CI). Use
`--input file --output file` — this path works everywhere and is what the
project's automated checks use.

**Nothing arrives on the other side / call hangs after handshake.** Almost
always a firewall blocking the UDP port (the GUI's *Received* counter stays at 0). The demo uses the *same* port number
for the TCP handshake and the listener's UDP voice traffic — open that UDP
port (not just TCP) in the firewall on the listener's machine. The caller's
UDP port is ephemeral and is negotiated automatically over the TCP channel
after the handshake, so nothing needs to be opened on the caller's side beyond
outbound UDP.

**`unpinned identity` / handshake fails.** Both sides must point
`--identity-file` at the exact same file generated by `keygen`. A regenerated
file invalidates any previous one for both roles.
