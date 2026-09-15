"""Exercise the actual frozen CLI with a real handshake and two-way WAV audio.

Usage: python scripts/smoke_package.py /path/to/hpqv-cli[.exe]
No third-party Python dependencies required by this test driver.
"""
import array
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import wave


def smoke(executable):
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise RuntimeError(f"Packaged executable missing: {executable}")
    env = os.environ.copy()
    for key in ("OQS_INSTALL_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
                "PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH"):
        env.pop(key, None)
    # Remove developer tools/library paths. A frozen app must stand alone.
    env["PATH"] = (str(Path(env.get("SYSTEMROOT", "C:/Windows")) / "System32")
                   if sys.platform == "win32" else "/usr/bin:/bin")
    with tempfile.TemporaryDirectory(prefix="hpqv packaged test ") as tmp:
        work = Path(tmp)
        subprocess.run([str(executable), "keygen", "--out", str(work / "identity.json")],
                       cwd=work, env=env, check=True, timeout=30)
        samples = array.array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / 48000))
                                   for i in range(48000 * 3)))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(work / "input.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(samples.tobytes())
        # Reserve TCP and check UDP on the same port before launching the listener.
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            udp.bind(("127.0.0.1", port))
        common = ["--identity-file", str(work / "identity.json"),
                  "--input", "file", "--wav", str(work / "input.wav"),
                  "--output", "file"]
        processes = []
        try:
            with (work / "listener.log").open("w") as listener_log, (work / "caller.log").open("w") as caller_log:
                listener = subprocess.Popen(
                    [str(executable), "listen", "--port", str(port), *common,
                     "--out-wav", str(work / "listener.wav")],
                    stdout=listener_log, stderr=subprocess.STDOUT, cwd=work, env=env)
                processes.append(listener)
                deadline = time.monotonic() + 20
                while f"listening on port {port}" not in (work / "listener.log").read_text():
                    if listener.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Listener did not become ready")
                    time.sleep(0.1)
                caller = subprocess.Popen(
                    [str(executable), "call", f"127.0.0.1:{port}", *common,
                     "--out-wav", str(work / "caller.wav")],
                    stdout=caller_log, stderr=subprocess.STDOUT, cwd=work, env=env)
                processes.append(caller)
                for process in processes:
                    if process.wait(timeout=30) != 0:
                        raise RuntimeError("Packaged peer exited with an error")
            for role in ("listener", "caller"):
                log = (work / f"{role}.log").read_text()
                if "handshake complete" not in log:
                    raise RuntimeError(f"{role}: handshake missing")
                with wave.open(str(work / f"{role}.wav"), "rb") as wav:
                    if wav.getframerate() != 48000 or wav.getnframes() < 48000:
                        raise RuntimeError(f"{role}: less than one second of received audio")
                    audio = array.array("h", wav.readframes(wav.getnframes()))
                    if not any(abs(sample) > 500 for sample in audio):
                        raise RuntimeError(f"{role}: received silence")
                print(f"PASS {role}: authenticated handshake and audible decoded WAV")
        except Exception:
            for log in work.glob("*.log"):
                print(log.name, log.read_text(), file=sys.stderr)
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    smoke(sys.argv[1])

