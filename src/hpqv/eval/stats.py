def drop_rate(sent: int, received_seqs) -> float:
    if sent <= 0: return 0.0
    return (sent - len(set(received_seqs))) / sent

def mean_jitter_ms(arrivals, frame_ms: float = 20.0) -> float:
    # RFC 3550 interarrival jitter, in ms.
    if len(arrivals) < 2: return 0.0
    j = 0.0
    prev = arrivals[0]
    for t in arrivals[1:]:
        d = abs((t - prev) * 1000.0 - frame_ms)
        j += (d - j) / 16.0
        prev = t
    return j

def ttfb_ms(connect_t: float, first_frame_t: float) -> float:
    return (first_frame_t - connect_t) * 1000.0


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson score interval for a proportion, as percentages.

    A success rate measured over n attempts is a sample, not a constant. Quoting
    a bare "19%" invites a re-run to contradict the report; quoting the interval
    does not. Wilson is used rather than the normal approximation because it
    stays sane near 0% and 100%, which is exactly where these measurements live.
    """
    if n <= 0:
        return (0.0, 100.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, 100.0 * (centre - half)), min(100.0, 100.0 * (centre + half)))
