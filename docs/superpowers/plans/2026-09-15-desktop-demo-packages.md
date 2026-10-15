# Desktop demo packages implementation plan

> Execute inline using superpowers:executing-plans. The user approved implementation in this session.

**Goal:** Deliver portable Windows x64 and Apple Silicon macOS GUI/CLI packages for LAN calls.
**Architecture:** Reuse hpqv.demo and hpqv.gui. Build pinned native libraries with CMake, then freeze both entry points with PyInstaller and smoke-test extracted archives.
**Tech Stack:** Python 3.11, tkinter, PyInstaller, CMake, liboqs 0.16.0, Opus 1.5.2, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-09-15-desktop-demo-design.md

## Constraints
- Windows x64 and macOS arm64; native builds.
- No Python/Homebrew prerequisite for testers.
- Never distribute an identity or private key.
- No protocol redesign, network discovery, relay, or accounts.
- Automated WAV transport checks do not establish real two-device microphone quality.

## Task 1: Identity setup and launchers
Files: src/hpqv/demo.py, src/hpqv/gui.py, src/hpqv/__init__.py, tests/test_demo_identity.py, packaging/cli_entry.py, packaging/gui_entry.py.
- [x] Add a failing real-key test for demo.create_identity(path); verify opposite-role key pinning and refusal to overwrite.
- [x] Extract generation into create_identity; route CLI and GUI through it.
- [x] Add Create identity with a save dialog and errors in the GUI.
- [x] Route the hpqv console script to demo.main and add GUI entry point.
- [x] Run identity and existing transport tests.

## Task 2: Native packaging and executable verification
Files: packaging/build.py, packaging/hpqv.spec, packaging/runtime_hook.py, packaging/requirements.txt, scripts/smoke_package.py, .github/workflows/desktop-demo.yml.
- [x] Write executable smoke check first. Generate a temporary identity and a 48 kHz mono PCM tone, spawn listen/call as separate processes, require nonempty received WAVs and a successful handshake.
- [x] Build pinned liboqs and Opus from upstream source with shared libraries and no OpenSSL dependency.
- [x] Freeze GUI and CLI, bundle native libs and metadata, fix ctypes lookup to exact bundled libraries.
- [x] Bundle runtime licenses, startup guide and build information; preserve macOS app symlinks in ZIP.
- [x] Build locally on arm64 and smoke-test an extracted path with spaces and no library environment variables.
- [x] Add native Windows/macOS CI builds and the same smoke checks before artifact upload.

## Task 3: Delivery verification
Files: docs/DESKTOP-DEMO.md, README.md, .gitignore.
- [x] Document download/extract/open, private identity exchange, local IP lookup, TCP/UDP firewall and OS microphone permissions, CLI commands and known signing limitations.
- [x] Verify macOS GUI launch.
- [ ] Manual Create identity click-through: desktop automation access to HPQV was not granted. Shared identity creation is covered by real-key tests.
- [x] Run relevant regression suite; inspect artifact contents for accidental private identities.
- [x] Record artifact paths and exact verification status, including any unavailable Windows build or hardware checks.


## Verification record — 2026-09-15

Build code: 5818402. [Native CI run](https://github.com/rabbive/hybrid-pqc-voice-transport/actions/runs/34949139427) succeeded on Windows x64 and macOS arm64.

- Local full suite with matching liboqs 0.16.0: 40 passed, 15 container-only skips. One pre-existing Scapy deprecation warning.
- Native CI: 21 transport/identity tests on each OS; both extracted packages passed real authenticated, bidirectional, non-silent WAV exchange with developer library paths removed.
- Runtime hook loads the bundled PortAudio library without opening microphone/speaker streams. Smoke checks require native and Python license files.
- Local Apple Silicon build: extracted ZIP passed the same smoke check; app passed deep/strict codesign verification and launched outside the terminal sandbox. Terminal-sandbox Launch Services failure was environmental, not a missing executable.
- Archive integrity, SHA-256 checksums and absence of a bundled identity.json checked during delivery.
- Deliverables: dist/HPQV-macos-arm64.zip and dist/HPQV-windows-x64.zip, with adjacent .zip.sha256 files. The Mac deliverable was built and tested locally; the Windows deliverable comes from the successful native CI run.
- Real two-computer microphone/speaker tests, GUI click-through, and mixed-platform hardware calls remain manual checks. See docs/DESKTOP-DEMO.md.
