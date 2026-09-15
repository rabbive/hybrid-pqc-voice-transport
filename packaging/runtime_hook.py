"""Load only our bundled Opus/liboqs, before their ctypes-based wrappers import."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys

bundle = Path(sys._MEIPASS)
names = (
    {"oqs": "oqs.dll", "opus": "opus.dll"}
    if sys.platform == "win32"
    else {"oqs": "liboqs.dylib", "opus": "libopus.dylib"}
)
native_paths = {name: str(bundle / filename) for name, filename in names.items()}
if sys.platform == "win32":
    # Retain the handle for the process lifetime.
    _hpqv_dll_directory = os.add_dll_directory(str(bundle))
# Fail before importing oqs if packaging is broken; never trigger its auto-install.
_hpqv_native_handles = [ctypes.CDLL(path) for path in native_paths.values()]
_original_find_library = ctypes.util.find_library


def bundled_find_library(name):
    if name in ("oqs", "liboqs"):
        return native_paths["oqs"]
    if name in ("opus", "libopus"):
        return native_paths["opus"]
    return _original_find_library(name)


ctypes.util.find_library = bundled_find_library
# liboqs logs at import time; GUI builds have no console streams.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

