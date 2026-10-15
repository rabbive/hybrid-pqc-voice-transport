# Run through packaging/build.py, which supplies HPQV_NATIVE_PREFIX.
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
prefix = Path(os.environ["HPQV_NATIVE_PREFIX"])
is_mac = sys.platform == "darwin"
names = ["liboqs.dylib", "libopus.dylib"] if is_mac else ["oqs.dll", "opus.dll"]
native = prefix / ("lib" if is_mac else "bin")
binaries = [(str(native / name), ".") for name in names]
datas = copy_metadata("liboqs-python")
for entry, name, console in [
    ("cli_entry.py", "hpqv-cli", True),
    ("gui_entry.py", "HPQV", False),
]:
    a = Analysis(
        [str(root / "packaging" / entry)],
        pathex=[str(root / "src")],
        binaries=binaries,
        datas=datas,
        hiddenimports=["sounddevice", "numpy"],
        runtime_hooks=[str(root / "packaging" / "runtime_hook.py")],
        excludes=["matplotlib", "scapy", "pytest", "hpqv.eval"],
    )
    pyz = PYZ(a.pure)
    exe = EXE(
        pyz, a.scripts, [], exclude_binaries=True, name=name,
        debug=False, bootloader_ignore_signals=False, strip=False,
        upx=False, console=console,
        target_arch="arm64" if is_mac else None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
    if is_mac and not console:
        app = BUNDLE(
            coll, name="HPQV.app", bundle_identifier="org.hpqv.demo",
            version="0.1.0",
            info_plist={
                "NSMicrophoneUsageDescription": "HPQV uses your microphone for the voice transport demo.",
                "NSLocalNetworkUsageDescription": "HPQV connects to your other computer for a voice demo on your local network.",
                "NSHighResolutionCapable": True,
            },
        )

