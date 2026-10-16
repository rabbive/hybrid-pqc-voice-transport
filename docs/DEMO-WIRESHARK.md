# Showing the call in Wireshark

What this demonstrates, and what it does not:

- **It shows** that the handshake is TLS 1.3 with the X25519MLKEM768 hybrid key
  exchange, that it travels over TCP (segmented, never IP-fragmented), and that
  every voice packet is a small UDP datagram whose payload is ciphertext.
- **It does not show** that nobody is in the middle. A man in the middle's
  traffic looks just as encrypted. The code both people read aloud (the SAS,
  shown in the incoming-call prompt and the Security tab) is what rules that out.

## Install

```bash
brew install --cask wireshark     # GUI; its installer adds ChmodBPF so you can capture without sudo
# or, CLI only:  brew install wireshark   (tshark; live capture then needs sudo)
```

Install the hpqv dissector, so voice packets show as `HPQV` with labelled header fields:

```bash
mkdir -p ~/.local/lib/wireshark/plugins && cp tools/wireshark/hpqv.lua ~/.local/lib/wireshark/plugins/
```

(`wireshark -X lua_script:tools/wireshark/hpqv.lua` also works for a one-off session.
With tshark, `-X lua_script:` loads too late for `-Y hpqv`; use the plugin folder,
or `WIRESHARK_PLUGIN_DIR=<folder holding hpqv.lua>`.)

## Capture

- **Two laptops:** capture on the Wi-Fi/Ethernet interface (usually `en0`) of
  either laptop, capture filter `host <other laptop's IP>`.
- **Rehearsing on one Mac:** run both GUIs with 127.0.0.1 and capture on `lo0`.
- **No capture rights at all:** build a sample from real protocol bytes and open it:

  ```bash
  uv run python tools/wireshark/make_sample_pcap.py hpqv-sample.pcap
  wireshark hpqv-sample.pcap
  ```

  The handshake, READY, CALL_START and the voice datagrams in it are real output
  of OpenSSL and the hpqv Keyring; only the IP/TCP/UDP framing is synthesized.
  It uses the GUI's default port, 9000. Wireshark does not expect TLS there, so use
  *Analyze → Decode As… → TCP port 9000 → TLS* (or `-d tcp.port==9000,tls` with tshark).
  The same applies to a live capture on whatever port the listener chose.

## What to point at

| # | Display filter | What the panel sees |
|---|---|---|
| 1 | `tls.handshake.type == 1` | The ClientHello. *key_share* carries an **X25519MLKEM768 (0x11ec)** share of 1216 B, listed first (OpenSSL also offers plain x25519). *signature_algorithms* includes **mldsa65 (0x0905)**. Wireshark 4.6 names both; older builds may print the numbers only. |
| 2 | `tls.handshake.type == 2` | The ServerHello: the chosen group is **X25519MLKEM768**. The app closes the connection if any other group is negotiated. |
| 3 | `tls.app_data` | Everything after the ServerHello is encrypted, **including both ML-DSA-65 certificates**: an eavesdropper cannot even see whose certificate it is. *Statistics → Conversations → TCP* shows the size: about 21 kB over TCP in the sample capture (10 kB one way, 11 kB the other), many times a 1500-byte MTU. |
| 4 | `ip.flags.mf == 1 or ip.frag_offset > 0` | **Nothing.** No IP fragments: TCP segments the big handshake itself. |
| 5 | `hpqv` | One line per voice packet: `hpqv v2 voice seq=… gen=… len=…`. Header fields (sequence, session id, key generation) are readable; the payload is labelled *Encrypted Opus frame + Poly1305 tag*. |
| 6 | `udp.length > 1208` | **Nothing.** No voice datagram exceeds the 1200-byte payload cap. |
| 7 | *Telephony → RTP → RTP Streams* | **No streams.** There is no plaintext RTP for Wireshark to pick up and play, unlike ordinary VoIP. |
| 8 | `hpqv.gen` column | Press **Rotate keys** in the app: the key generation steps 0 → 1 → 2 while the audio keeps playing. |

The app's Security tab panel *What an eavesdropper sees* shows the same bytes as
the latest `hpqv` packet: compare its sequence number and first payload bytes
with Wireshark's packet details.

## If something looks wrong

- **No `HPQV` packets, only `UDP`:** the dissector is not loaded (check
  *Help → About → Plugins*), or the packets are not v2 (a v1 peer).
- **Group shows as a number, not a name:** older Wireshark; 0x11ec is still X25519MLKEM768.
- **No TLS at all, or odd protocol names on the listener's port:** use Decode As
  on that port (above), and start capturing before pressing Call.
