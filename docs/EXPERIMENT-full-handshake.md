# Full authenticated handshake under loss

## Method

This experiment runs the real ML-KEM-768 and ML-DSA-65 authenticated handshake
over TCP, one-shot UDP, and retrying UDP. Both peers use pinned identities. Each
attempt has the same 3 second deadline by default; `--deadline` changes it. The
identities are generated once per measurement series, outside attempt timing.
The HELLO and ACCEPT each fit in one UDP datagram, which IP fragments on a
1500-byte MTU. TCP uses the existing length-prefixed control channel.

The retrying UDP initiator resends its HELLO every 250 ms. The responder reuses
the ACCEPT created for that HELLO, preserving the encapsulated session key.
Success requires both peers to finish before the deadline and agree on the key
and initiator-to-responder prefix.

## Difference from the synthetic experiment

The earlier experiment sent a random 10,842-byte blob and used a 1 second UDP
timeout versus a 20 second TCP timeout. This run performs signature checks,
KEM encapsulation and decapsulation, and uses one deadline for all transports.

## Caveats

- Results use loopback, not a real voice call or a physical network.
- Linux `tc netem` applies independent random packet loss; real loss can be
  bursty and path-dependent.
- A shared deadline measures completion under one chosen time budget. It does
  not capture every latency or user experience tradeoff.
- UDP retry resends HELLO every 250 ms, while Linux TCP SYN retransmission is
  typically around 1 second and then 3 seconds (the exact schedule depends on
  kernel settings). At the default 3 second deadline, TCP versus UDP-retry
  largely compares retry schedules. The results can support a claim about
  completion under these schedules and this deadline; they do not show that
  either transport is inherently faster or more reliable under other deadlines
  or networks.
- UDP fragmentation counts assume a 1500-byte MTU and IPv4 headers.

## Results

Run on 2026-10-15 in the `hpqv-eval` container (Linux arm64, loopback MTU pinned
to 1500, i.i.d. `tc netem` loss), 100 attempts per cell, one common 3 s deadline.
HELLO is 6,446 B and ACCEPT 4,398 B, so each UDP attempt is 8 IP fragments.
Source: `results/full_handshake.csv`, figure `results/full_handshake.png`.

| Loss | TCP success | UDP naive success | (1−p)^8 prediction | UDP retry success |
|---:|---:|---:|---:|---:|
| 0% | 100% (1.0 ms) | 100% (3.4 ms) | 100% | 100% (3.1 ms) |
| 5% | 100% (1.2 ms) | 72% (3.5 ms) | 66% | 100% (4.3 ms) |
| 10% | 99% (2.2 ms) | 44% (3.5 ms) | 43% | 100% (257 ms) |
| 20% | 84% (4.2 ms) | 18% (2.8 ms) | 17% | 86% (770 ms) |
| 30% | 56% (1,017 ms) | 6% (4.9 ms) | 6% | 52% (1,269 ms) |

Times in brackets are the median over successful attempts only.

What this supports:

- Sending the real handshake as one-shot UDP datagrams fails the way fragment
  arithmetic predicts: success tracks (1−p)^8 within sampling error at every level.
- Carrying the same handshake over TCP completes 84% of the time at 20% loss and
  56% at 30%, against 18% and 6% for naive UDP, under the same 3 s budget.
- A UDP design that retransmits the whole HELLO every 250 ms performs about as
  well as TCP at this deadline (86% vs 84% at 20%; 52% vs 56% at 30%). The gain
  over naive UDP comes from retransmission, not from TCP specifically.

What it does not support:

- It does not show TCP beats every reliable-UDP design. The retry baseline
  matches it here, at the cost of resending all 6.4 KB (8 fragments) on any
  single fragment loss and of depending on IP fragments reaching the peer, which
  this loopback test does not challenge.
- These figures are lower than the earlier synthetic experiment (99% at 20%,
  83% at 30%) because that one used a 20 s TCP timeout and a random payload.
  Quote these numbers when the claim is about completing a real call setup.

## Reproduce

```sh
PYTHONPATH=src python scripts/run_full_handshake.py
```

Use `--quick` for five attempts per transport and loss level, `--attempts 30`
to choose the attempts per transport and loss level, or `--deadline 5.0` to set
a five second common deadline. The script prints a worst-case runtime estimate
before starting. Run inside the
`hpqv-eval` container with `NET_ADMIN` so the script can set loopback MTU and
apply `tc netem`.
