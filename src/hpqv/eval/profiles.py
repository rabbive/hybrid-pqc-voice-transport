"""Named network-impairment profiles for the seeded loopback proxy."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    loss_pct: float = 0
    loss_model: str = "random"
    burst_len: float = 3
    delay_ms: float = 0
    jitter_ms: float = 0
    dup_pct: float = 0
    reorder_pct: float = 0
    bandwidth_kbps: float = 0  # 0 = unlimited
    outages: tuple[tuple[float, float], ...] = ()  # (start_s, duration_s)

    def __post_init__(self):
        if self.loss_model not in ("random", "burst"):
            raise ValueError(f"unknown loss model: {self.loss_model}")
        for pct in (self.loss_pct, self.dup_pct, self.reorder_pct):
            if not 0 <= pct <= 100:
                raise ValueError("percentages must be within 0..100")
        if self.burst_len < 1:
            raise ValueError("burst_len must be >= 1")
        if min(self.delay_ms, self.jitter_ms, self.bandwidth_kbps) < 0:
            raise ValueError("delay, jitter and bandwidth must be >= 0")
        if any(start < 0 or duration < 0 for start, duration in self.outages):
            raise ValueError("outages must be non-negative (start_s, duration_s)")


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile("clean"),
        Profile("loss1", loss_pct=1),
        Profile("loss5", loss_pct=5),
        Profile("loss10", loss_pct=10),
        Profile("loss20", loss_pct=20),
        Profile("burst10", loss_pct=10, loss_model="burst", burst_len=3),
        Profile("high_latency_jitter", delay_ms=150, jitter_ms=40),
        Profile("outage", outages=((3.0, 2.0),)),
    )
}
