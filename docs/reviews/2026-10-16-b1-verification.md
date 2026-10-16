# Phase B1 verification: what was checked, and how

Date: 2026-10-16. Branch `feat/secure-session-v2`, fixes in 61eadd1 and the
interop test in 0e9206d. Every result below came from a command run on this
date. Nothing here is an external audit.

## 1. Library stack actually in use

| Component | Version | How checked |
|---|---|---|
| Python | 3.11.5 (macOS), 3.11.16 (Linux container) | `python -V`, `sys.version` |
| `cryptography` | 50.0.1, bundling **OpenSSL 4.0.2 (25 Aug 2026)** | `cryptography.__version__`, `SSL.OpenSSL_version()` |
| pyOpenSSL | 26.4.0, on that same bundled OpenSSL | `OpenSSL.__version__` |
| liboqs | **not installed** | `import oqs` → `ModuleNotFoundError` on both platforms |

TLS, ML-KEM-768, ML-DSA-65 and X25519 all run inside that one OpenSSL 4.0.2.
The system OpenSSL 3.0.x that Python's `ssl` links to is not used.

## 2. What is negotiated, observed on the wire

- **Method:** `tests/test_t6_regressions.py::test_t6_clienthello_offers_only_x25519mlkem768_and_mldsa65`.
  It parses the real ClientHello and ServerHello records that cross a loopback proxy.

| Field | Observed | Meaning |
|---|---|---|
| ClientHello `supported_groups` | `[0x11EC]` only | X25519MLKEM768; no classical group offered |
| ClientHello `key_share` | one entry, `0x11EC`, 1,216 B | ML-KEM-768 encapsulation key (1,184) plus X25519 (32), the size RFC 10024 specifies |
| ClientHello `signature_algorithms` | `[0x0905]` only | mldsa65 |
| ServerHello `key_share` | `0x11EC`, 1,120 B | ML-KEM-768 ciphertext (1,088) plus X25519 (32) |
| Negotiated (both peers OpenSSL 4.0.2) | TLSv1.3, `X25519MLKEM768`, `TLS_AES_256_GCM_SHA384`, ALPN `hpqv/2` | `SecureChannel` attributes |

- **Before the fix:** the ClientHello offered 11 groups, 21 signature
  algorithms and a classical X25519 key share (T6 finding 3).
- **A peer offering only P-256** is now refused during the handshake
  (`tls_error`, alert `handshake_failure`). The post-handshake `wrong_group`
  check remains as a backstop and has its own test.

## 3. Interop with an independent implementation (Go 1.27.1 `crypto/tls`)

`tests/test_interop_go.py` builds `tests/interop/go_peer/main.go`. That peer uses
Go's own TLS 1.3, ML-KEM, X25519 and ML-DSA code, which shares nothing with
OpenSSL. Result: **4 passed**.

| Case | Result |
|---|---|
| Our client ↔ Go server | TLS 1.3 (`0x0304`), group `0x11ec`, cipher `TLS_AES_128_GCM_SHA256`, ALPN `hpqv/2`. Go sees our cert as ML-DSA / `ML-DSA-65` and no resumption. All four `EXPORTER-hpqv2 …` values are byte-equal on both sides. |
| Go client ↔ our server | The same, with mutual ML-DSA-65 authentication in both directions. |
| Go client offering only X25519 (classical) | Refused (`tls_error`); no channel is created. |
| Go server presenting an unpinned ML-DSA key | Refused (`bad_peer_cert`). |

**What this establishes:**
- **Same derivation.** Two unrelated implementations derive the same
  X25519MLKEM768 shared secret and the same TLS 1.3 key schedule and exporter.
  If the share layout or the order of the secrets differed, the handshake or
  the exporter values would not match.
- **ML-DSA-65 signatures** in CertificateVerify, using the 0x0905 codepoint, are
  verified in both directions.
- **Empty versus absent exporter context.** We pass an empty context (`b""`);
  Go passes `nil`. They give the same output, which answers T6's question about
  RFC 8446 §7.5.

**What it does not establish:** full conformance. No TLS conformance suite
(tlsfuzzer, TLS-Attacker, BoGo) was run. Two implementations agreeing is
evidence, not proof.

## 4. Certificates and ML-DSA integration

- **Encoding (`openssl asn1parse` of a cert from `identity.new`):** SPKI and
  signature algorithm are both `ML-DSA-65` (2.16.840.1.101.3.4.3.18) with
  **no parameters field**. The public key BIT STRING is 1,953 B (1,952 + 1
  unused-bits byte), and the signature is 3,310 B (3,309 + 1). This matches RFC
  9881's encoding rules.
- **Algorithm checks:** `identity` accepts only ML-DSA-65 keys and certs. The
  pin compares raw ML-DSA public keys, both in the verify callback and again
  after the handshake.
- **Primitive correctness:** `tests/test_pqc_kat.py` runs NIST ACVP vectors
  against `cryptography`'s ML-KEM-768 (keyGen, decapsulation including implicit
  rejection, encapsulation-key check) and ML-DSA-65 (keyGen, sigVer pure and
  external-mu). Its version lives in the same bundled OpenSSL that runs TLS.

## 5. Standards, and what is and is not claimed

Statuses were checked on the IETF datatracker on 2026-10-16.

| Spec | Status | What we claim |
|---|---|---|
| RFC 9846 (TLS 1.3) | RFC | OpenSSL runs the handshake; we check TLS 1.3 is negotiated |
| RFC 10024 (ECDHE-MLKEM / X25519MLKEM768) | RFC, Standards Track | Codepoint, share sizes and Go interop as above. **Not** a claim of full conformance |
| RFC 9954 (hybrid design for TLS 1.3) | RFC | Background only |
| RFC 9881 (ML-DSA in X.509) | RFC, Proposed Standard | The cert encoding checks in §4 |
| draft-ietf-tls-mldsa-06 (ML-DSA in TLS, 0x0905) | **Internet-Draft, in the RFC Editor queue. Not an RFC.** | Interop with Go and OpenSSL only. The codepoint could still change before publication |
| FIPS 203 / FIPS 204 | Final | ACVP vectors pass for the library we use |

## 6. Platform coverage

| Platform | What ran | Result |
|---|---|---|
| macOS 27.0.1, arm64, Python 3.11.5 | full suite (`--ignore=tests/test_gui_window.py`) | **498 passed, 15 skipped**. The skips need Linux `tc`/`tshark`/ns-3 |
| Linux: Ubuntu 24.04 aarch64 in Docker (`hpqv-eval` image, `--cap-add=NET_ADMIN`), Python 3.11.16 | full suite on an exported copy of HEAD 0e9206d | **508 passed, 5 skipped**. The skips are 4 Go interop tests (no Go in the image) and 1 test needing libopus ≥ 1.5. The 15 netem/tshark tests ran and passed |
| Windows | **not run.** No Windows host, and CI was not triggered (nothing pushed) | Windows-only behaviour is exercised only with fakes, listed below |
| Tk window tests (`tests/test_gui_window.py`) | not run; they need an interactive display | — |

Windows-specific code that has been tested only with fakes, never on Windows:
- **`identity._restrict_windows_acl`:** the `whoami /user` and `icacls /inheritance:r /grant:r *SID:F` calls.
- **`identity._windows_sddl`:** the `icacls /save` call, the UTF-16 file and taking the second line.
  The SDDL parser itself (`_broad_allow_aces`) is pure and is tested on sample strings.
- **`udp.open_udp`:** `SIO_UDP_CONNRESET`.
- **The WSAEMSGSIZE and WSAECONNRESET handling** in the receive loops (a fake Windows-like socket).
- **`os.fchmod` avoided on Windows.** It does not exist there on Python 3.11,
  and v2 `identity.save` would have crashed. This was found during B1.

Running the suite on Windows (the `desktop-demo.yml` job has a `windows-2022`
runner) is required before Windows can be called supported.

## 7. Mutation check of the B1 fixes

Each fix was reverted in turn and the regression tests re-run. **12 of 12
mutants were caught:**
- group restriction removed;
- signature-algorithm restriction removed;
- own identity allowed in `establish`;
- own identity allowed in the CLI loader;
- no POSIX mode check;
- no owner check;
- broad Windows ACL ignored;
- reusable media keys;
- a 1,201 B receive buffer;
- WSAECONNRESET ending the call;
- WSAEMSGSIZE ending the call;
- no `SIO_UDP_CONNRESET`.
