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

Fill this table from the container run.

| Loss | TCP success | UDP naive success | UDP retry success |
|---:|---:|---:|---:|
| 0% | — | — | — |
| 5% | — | — | — |
| 10% | — | — | — |
| 20% | — | — | — |
| 30% | — | — | — |

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
