# Desktop demo distribution

Approved in chat on 2026-09-15. Target Windows x64 and macOS Apple Silicon, on the same LAN, including mixed-platform peers.

Ship portable ZIPs with the existing GUI and CLI, Python 3.11, liboqs 0.16.0, Opus 1.5.2 and PortAudio. macOS includes HPQV.app, Windows HPQV.exe. No developer tools are required on testers' machines. Build each artifact natively; do not label an unexecuted build tested.

Add Create identity to the GUI, using the same identity format as the CLI. Save to a user-selected location and select the generated file. Generate fresh identities locally, never embed private identities in artifacts. Users privately copy the same identity file to their other machine. Preserve the current demo trust model.

Keep Listen/Call, local IP entry, live loss control, audio meters and counters. Include CLI commands and a plain-language LAN guide covering OS microphone permission, firewall permissions, guest-network isolation, and headphone use. No relay, discovery, account, or NAT traversal.

Use PyInstaller with explicit native-library collection and deterministic bundled loading. Include upstream licenses and Python distribution metadata. Supply repeatable build automation for Windows and macOS arm64. macOS has microphone usage text and ad-hoc signing; commercial signing/notarization requires credentials and is outside this demo build.

Verify identity pairing, existing transport tests, each packaged CLI's handshake and bidirectional WAV transfer, and macOS GUI launch. Each ZIP is extracted to a path containing spaces before its smoke check. Human two-device mic/speaker tests remain separate from automated checks.

