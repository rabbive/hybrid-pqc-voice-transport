# HPQV desktop demo

Use two computers on the same normal Wi-Fi/LAN. Windows x64 and Apple Silicon Macs can call each other. Use the same build version on both devices.

## Open the package

- Windows 10/11 x64: extract the entire HPQV-windows-x64.zip, then open HPQV/HPQV.exe. Keep its _internal folder beside it.
- Apple Silicon Mac, macOS 14 or newer: extract HPQV-macos-arm64.zip and open HPQV.app. You can drag the app into Applications. The separate hpqv-cli folder is optional for GUI users.
- No Python, Homebrew, compiler, or terminal setup is needed to use the GUI.

These are development builds. Windows is unsigned; macOS is ad-hoc signed, not Apple-notarized. Download only the build supplied by your project teammate. If macOS blocks it, use the app-specific Open Anyway option in System Settings > Privacy & Security after attempting to open it. See [Apple's instructions](https://support.apple.com/en-gb/102445). Do not disable system security. On managed machines, ask the administrator if local policy blocks development apps.

## Make the first call

1. Wear headphones on both computers to avoid feedback.
2. Open HPQV on both. On computer A, click Create identity and save a NEW identity.json somewhere you can find it.
3. Privately copy that file to computer B, for example via USB or AirDrop. It contains secret keys for both demo roles. Do not post it publicly or include it in a software ZIP.
4. On B, use the … button to select that copied identity. A selects its new file automatically.
5. Find computer A's Wi-Fi IPv4 address:
   - Windows: Settings > Network & Internet > Wi-Fi > connected network properties, or run ipconfig and read the Wi-Fi adapter's IPv4 Address.
   - Mac: System Settings > Wi-Fi > Details > TCP/IP, or read the Wi-Fi IP in Network settings.
6. On A, choose Listen on port, leave 9000, and click Start.
7. On B, choose Call, enter A's address followed by :9000, such as 192.168.1.42:9000, and click Start. Never use the example address unless it is actually A's address.
8. Allow microphone and local-network access if prompted. Allow HPQV through the firewall on your trusted private network.
9. Both windows should say connected. Talk in both directions and watch the in/out meters and received counter.
10. Move the loss slider from 0% to 20–30%, then back to 0%. It drops outgoing voice packets on that computer. Record what you hear and the counters; high loss may cause substantial degradation.
11. Click Hang up on both peers after the test.

Generate the identity only once per pair of tests. Two independently generated files will not authenticate.

## If the call does not work

- Same Wi-Fi name is not sufficient on a guest/hotel/campus network that isolates clients. Use a normal LAN that permits devices to communicate.
- Check that B targets A's current Wi-Fi IP, with :9000, and that A started Listen first.
- The listener uses TCP 9000 for control and UDP 9000 for voice. Allow both on A's private-network firewall. The caller receives UDP on a dynamically chosen port; allow the application on B too. You do not need router port forwarding for a normal LAN.
- A completed handshake with a stationary received counter usually indicates blocked UDP.
- Enable microphone access for HPQV in OS privacy settings. CLI users may also need permission for their terminal. Select the intended default microphone and headphones in OS sound settings before opening the app.
- If a canceled or failed call leaves the window stuck, close and reopen HPQV on both computers before retrying.
- Use the same identity file and same build version on both computers.
- GUI diagnostic log: ~/Library/Logs/HPQV/demo.log on Mac, or %LOCALAPPDATA%/HPQV/demo.log on Windows. Logs are local; share only after reviewing their contents.

## CLI

Open a terminal in the extracted package folder. On Windows PowerShell:

```powershell
.\hpqv-cli\hpqv-cli.exe keygen --out identity.json
.\hpqv-cli\hpqv-cli.exe listen --port 9000 --identity-file identity.json --seconds 300
# On the other computer, after privately copying identity.json:
.\hpqv-cli\hpqv-cli.exe call 192.168.1.42:9000 --identity-file identity.json --seconds 300
```

On Mac:

```bash
./hpqv-cli/hpqv-cli keygen --out identity.json
./hpqv-cli/hpqv-cli listen --port 9000 --identity-file identity.json --seconds 300
# On the other computer:
./hpqv-cli/hpqv-cli call 192.168.1.42:9000 --identity-file identity.json --seconds 300
```

Add --drop-pct 30 for fixed loss in CLI mode. GUI loss changes live. GUI calls run up to one hour; CLI defaults to 30 seconds unless --seconds is set. --help lists file-input/output options.

## Suggested two-device check

For each pairing you have available, Windows–Windows, Mac–Mac, Windows–Mac:

- Test a clean call in both directions for a minute.
- Repeat with each computer acting as listener.
- Change loss during the call, then return to 0%.
- Hang up, reopen, and reconnect.
- Record OS versions, app build, pairing, whether both directions worked, and any audio glitches.

A build passing the automated WAV test means the bundled crypto, codec and transport run. It does not prove microphone permissions, physical audio devices, your Wi-Fi firewall rules, or all mixed-platform pairings.

## Build from source

Build on Windows x64 with Visual Studio 2022 C++ build tools, CMake, Git and Python 3.11, or on Apple Silicon with Xcode command line tools, CMake, Git and a Python 3.11 installation that includes tkinter.

```text
python -m pip install -r packaging/requirements.txt
python -m pip install --no-deps .
python packaging/build.py
```

The build downloads the pinned liboqs 0.16.0 and Opus 1.5.2 source commits, builds shared libraries, and freezes the existing GUI/CLI. Native libraries are loaded from the package, not from a tester's Homebrew or Python setup. Build architecture must match the target; see [PyInstaller's platform notes](https://pyinstaller.org/en/stable/feature-notes.html).

ZIPs and SHA-256 checksums land in dist/. Extract a ZIP to a fresh path containing spaces, then run:

```text
python scripts/smoke_package.py "PATH TO EXTRACTED PACKAGE/hpqv-cli/hpqv-cli"
```

Use hpqv-cli.exe on Windows. The check removes developer library environment variables, creates temporary keys, and requires a real authenticated handshake and non-silent decoded WAV audio in both directions. Temporary test keys are deleted afterward.

The Desktop demo workflow builds and tests native Windows and macOS packages, then uploads ZIPs as Actions artifacts. GitHub Actions usage follows your account's allowance; no public release is created by this workflow.

