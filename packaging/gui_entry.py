"""Keep GUI diagnostics available even without a terminal."""
import os
from pathlib import Path
import sys

if sys.platform == "darwin":
    log_dir = Path.home() / "Library" / "Logs" / "HPQV"
else:
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HPQV"
log_dir.mkdir(parents=True, exist_ok=True)
log = (log_dir / "demo.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

from hpqv.gui import main

if __name__ == "__main__":
    main()

