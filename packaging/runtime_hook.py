"""Load only our bundled Opus, before its ctypes-based wrapper imports."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys

bundle = Path(sys._MEIPASS)
names = {"opus": "opus.dll" if sys.platform == "win32" else "libopus.dylib"}
native_paths = {name: str(bundle / filename) for name, filename in names.items()}
if sys.platform == "win32":
    # Retain the handle for the process lifetime.
    _hpqv_dll_directory = os.add_dll_directory(str(bundle))
# Fail before importing opuslib if packaging is broken.
_hpqv_native_handles = [ctypes.CDLL(path) for path in native_paths.values()]
_original_find_library = ctypes.util.find_library


def bundled_find_library(name):
    if name in ("opus", "libopus"):
        return native_paths["opus"]
    return _original_find_library(name)


ctypes.util.find_library = bundled_find_library
# GUI builds have no console streams.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Import initializes bundled PortAudio without opening a microphone or speaker.
# The file-only smoke check therefore catches a missing audio-device library too.
import sounddevice
