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
- [ ] Add a failing real-key test for demo.create_identity(path); verify opposite-role key pinning and refusal to overwrite.
- [ ] Extract generation into create_identity; route CLI and GUI through it.
- [ ] Add Create identity with a save dialog and errors in the GUI.
- [ ] Route the hpqv console script to demo.main and add GUI entry point.
- [ ] Run identity and existing transport tests.

## Task 2: Native packaging and executable verification
Files: packaging/build.py, packaging/hpqv.spec, packaging/runtime_hook.py, packaging/requirements.txt, scripts/smoke_package.py, .github/workflows/desktop-demo.yml.
- [ ] Write executable smoke check first. Generate a temporary identity and a 48 kHz mono PCM tone, spawn listen/call as separate processes, require nonempty received WAVs and a successful handshake.
- [ ] Build pinned liboqs and Opus from upstream source with shared libraries and no OpenSSL dependency.
- [ ] Freeze GUI and CLI, bundle native libs and metadata, fix ctypes lookup to exact bundled libraries.
- [ ] Bundle runtime licenses, startup guide and build information; preserve macOS app symlinks in ZIP.
- [ ] Build locally on arm64 and smoke-test an extracted path with spaces and no library environment variables.
- [ ] Add native Windows/macOS CI builds and the same smoke checks before artifact upload.

## Task 3: Delivery verification
Files: docs/DESKTOP-DEMO.md, README.md, .gitignore.
- [ ] Document download/extract/open, private identity exchange, local IP lookup, TCP/UDP firewall and OS microphone permissions, CLI commands and known signing limitations.
- [ ] Verify macOS GUI launch and Create identity interaction.
- [ ] Run relevant regression suite; inspect artifact contents for accidental private identities.
- [ ] Record artifact paths and exact verification status, including any unavailable Windows build or hardware checks.

