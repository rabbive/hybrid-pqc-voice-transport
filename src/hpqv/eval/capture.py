import subprocess
from contextlib import contextmanager


@contextmanager
def capture(iface: str, out_path: str):
    proc = subprocess.Popen(["dumpcap", "-i", iface, "-w", out_path])
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def count_ip_fragments(pcap_path: str) -> int:
    # A fragment = MF flag set OR non-zero fragment offset.
    out = subprocess.run(
        ["tshark", "-r", pcap_path, "-Y", "ip.flags.mf==1 || ip.frag_offset>0",
         "-T", "fields", "-e", "frame.number"],
        capture_output=True, text=True, check=True).stdout
    return sum(1 for line in out.splitlines() if line.strip())
