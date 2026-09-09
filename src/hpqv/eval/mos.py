# ITU-T G.107 E-model, simplified for a fixed narrowband codec baseline.
# Effective latency + an Ie-eff-style loss penalty (Opus-ish: Ie=1, Bpl=20).
def r_factor(delay_ms: float, jitter_ms: float, loss_pct: float) -> float:
    R0, Is = 93.2, 0.0
    eff_delay = delay_ms + 2.0 * jitter_ms          # jitter buffer adds ~2x jitter
    # Idd: delay impairment (approx, negligible <150ms, rises after)
    if eff_delay < 160:
        Id = 0.024 * eff_delay
    else:
        Id = 0.024 * eff_delay + 0.11 * (eff_delay - 120)
    Ie, Bpl = 1.0, 20.0                              # Opus-like packet-loss robustness
    Ie_eff = Ie + (95 - Ie) * (loss_pct / (loss_pct + Bpl)) if loss_pct > 0 else Ie
    return R0 - Is - Id - Ie_eff

def mos(r: float) -> float:
    if r < 0: return 1.0
    if r > 100: return 4.5
    m = 1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r)
    return max(1.0, min(4.5, m))

def mos_from_network(delay_ms: float, jitter_ms: float, loss_pct: float) -> float:
    return mos(r_factor(delay_ms, jitter_ms, loss_pct))
