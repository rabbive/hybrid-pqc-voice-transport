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
