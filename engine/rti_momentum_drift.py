"""
CFB RTI Momentum Divergence (CRMD) Strategy

Exploits divergence between CFB RTI momentum direction and Kalshi contract price.
In the final 180s of a 15-minute window, the CFB RTI (Real-Time Index) is the settlement
reference. If RTI is trending consistently toward/away from the strike and the Kalshi
contract price hasn't moved correspondingly, there's a mispricing opportunity.

Key insight: RTI momentum is a leading signal because the 60-second RTI average determines
the settlement price. Catching the drift early (180s window) gives better entry prices than
waiting for mathematical lock detection (final 60s).

Different from the existing settlement edge:
- Settlement edge: final 60s, uses avg_60s vs strike (mathematical lock)
- CRMD: final 180s, uses RTI momentum rate-of-change (soft edge, earlier entry)
"""

import time

from config import CFB_INDEX_MAP


class RTIMomentumDrift:
    """
    CFB RTI Momentum Divergence (CRMD) Strategy.

    Tracks CFB RTI momentum in the final 180s of each window. Enters when RTI
    momentum exceeds threshold AND Kalshi price hasn't caught up.
    """

    # Default parameters
    MOMENTUM_LOOKBACK = 180.0  # seconds to look back for momentum
    MOMENTUM_THRESHOLD_BPS = 0.5  # minimum bps/sec to trigger
    PRICE_DIVERGENCE_PCT = 0.03  # 3% divergence (safe, avoids single-tick noise)
    ENTRY_WINDOW_START = 180  # only active in final 180s
    ENTRY_WINDOW_END = 60  # stops at 60s (handoff to settlement edge)
    SIZING_MULTIPLIER = 2.0  # large conviction sizing

    def __init__(self, asset: str):
        self.asset = asset.upper()
        self.cfb_index_id = CFB_INDEX_MAP.get(self.asset)

        # Cache last signal to avoid spam
        self._last_signal_time = 0
        self._signal_cooldown = 30  # don't re-signal within 30s

    # ------------------------------------------------------------------
    # Momentum / RTI helpers
    # ------------------------------------------------------------------

    def get_rti_momentum(self) -> float | None:
        """
        Compute RTI momentum in bps/sec over the rolling lookback window.
        Uses shared CFB feed state. Returns None if feed unavailable.
        """
        if not self.cfb_index_id:
            return None

        from feed.cfb_state import compute_rti_momentum_bps

        return compute_rti_momentum_bps(self.cfb_index_id, self.MOMENTUM_LOOKBACK)

    def get_rti_vs_strike(self, strike: float) -> float | None:
        """
        Compute ratio of current RTI value to strike.
        >1.0 = RTI above strike (bullish), <1.0 = RTI below strike (bearish).
        """
        if not self.cfb_index_id or strike <= 0:
            return None

        from feed.cfb_state import get_rti_value_vs_strike

        return get_rti_value_vs_strike(self.cfb_index_id, strike)

    def get_current_rti(self) -> float | None:
        """Return the latest RTI value, or None."""
        if not self.cfb_index_id:
            return None
        from feed.cfb_state import get_value

        return get_value(self.cfb_index_id)

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------

    def _kalshi_price_implied_by_rti(
        self, rti_ratio: float, time_remaining: int
    ) -> float:
        """
        Estimate what the Kalshi YES price *should* be given the RTI's position
        relative to strike.

        Linear model around strike:
        - RTI at strike (ratio=1.0) → implied ~0.50
        - Leverage factor increases as time remaining shrinks
        """
        rti_dev_pct = rti_ratio - 1.0
        # Leverage: higher near expiry since RTI determines settlement
        leverage = 3.0 + (120.0 / max(time_remaining, 10))
        implied = 0.50 + (rti_dev_pct * 100 * leverage / 100)
        return max(0.01, min(0.99, implied))

    def evaluate(
        self,
        strike: float,
        kalshi_yes_price: float,
        time_remaining: int,
    ) -> dict | None:
        """
        Evaluate CRMD signal.

        Returns signal dict with direction, confidence, sizing multiplier, or None.
        Only fires in final 180s, above 60s (settlement edge's domain).
        """
        # --- Timing gate ---
        if time_remaining > self.ENTRY_WINDOW_START:
            return None
        if time_remaining < self.ENTRY_WINDOW_END:
            return None

        # --- Cooldown ---
        now = time.time()
        if now - self._last_signal_time < self._signal_cooldown:
            return None

        # --- RTI momentum ---
        rti_momentum = self.get_rti_momentum()
        if rti_momentum is None:
            return None

        if abs(rti_momentum) < self.MOMENTUM_THRESHOLD_BPS:
            return None

        # --- RTI position vs strike ---
        rti_ratio = self.get_rti_vs_strike(strike)
        if rti_ratio is None:
            return None

        # --- Direction: momentum and position must agree ---
        if rti_momentum > 0 and rti_ratio > 1.0:
            direction = "ENTER_YES"
        elif rti_momentum < 0 and rti_ratio < 1.0:
            direction = "ENTER_NO"
        else:
            # Momentum and position disagree — too ambiguous
            return None

        # --- Kalshi divergence check ---
        implied_price = self._kalshi_price_implied_by_rti(rti_ratio, time_remaining)
        divergence = abs(kalshi_yes_price - implied_price) / max(implied_price, 0.01)

        if divergence < self.PRICE_DIVERGENCE_PCT:
            # Kalshi price has already caught up — no mispricing edge
            return None

        # --- Confidence calculation ---
        momentum_confidence = min(
            1.0, abs(rti_momentum) / (self.MOMENTUM_THRESHOLD_BPS * 4)
        )
        rti_distance_confidence = min(1.0, abs(rti_ratio - 1.0) * 20)
        time_urgency = 1.0 - (time_remaining / self.ENTRY_WINDOW_START)

        combined_confidence = (
            momentum_confidence * 0.4
            + rti_distance_confidence * 0.3
            + time_urgency * 0.3
        )

        if combined_confidence < 0.3:
            return None

        combined_confidence = max(0.0, min(1.0, combined_confidence))

        self._last_signal_time = now

        reason = (
            f"CRMD: RTI momentum {rti_momentum:+.3f} bps/sec, "
            f"RTI/strike={rti_ratio:.5f}, "
            f"Kalshi ${kalshi_yes_price:.2f} vs implied ${implied_price:.2f} "
            f"({divergence:.1%} divergence)"
        )

        signal = {
            "direction": direction,
            "confidence": combined_confidence,
            "rti_momentum_bps": rti_momentum,
            "rti_vs_strike_ratio": rti_ratio,
            "kalshi_divergence": divergence,
            "time_remaining": time_remaining,
            "sizing_multiplier": self.SIZING_MULTIPLIER,
            "strategy": "CRMD",
            "reason": reason,
        }

        print(
            f"[CRMD] {self.asset} SIGNAL: {direction} | "
            f"conf={combined_confidence:.2f} | "
            f"rti_mom={rti_momentum:+.3f} bps/s | "
            f"rti/strike={rti_ratio:.5f} | "
            f"divergence={divergence:.1%}"
        )

        return signal

    def check_exit(
        self,
        strike: float,
        time_remaining: int,
        entry_direction: str,
    ) -> dict | None:
        """
        Check if an active CRMD position should exit early.

        Exit conditions:
        1. RTI momentum reverses direction
        2. time_remaining drops below ENTRY_WINDOW_END (60s, settlement edge handoff)
        """
        if time_remaining < self.ENTRY_WINDOW_END:
            return {
                "action": "EXIT",
                "reason": f"CRMD time exit: {time_remaining}s remaining (settlement edge handoff)",
            }

        rti_momentum = self.get_rti_momentum()
        if rti_momentum is None:
            return None

        # Momentum reversed from entry direction
        if entry_direction == "ENTER_YES" and rti_momentum < 0:
            return {
                "action": "EXIT",
                "reason": f"CRMD momentum reverse: {rti_momentum:+.3f} bps/s (was bullish)",
            }
        if entry_direction == "ENTER_NO" and rti_momentum > 0:
            return {
                "action": "EXIT",
                "reason": f"CRMD momentum reverse: {rti_momentum:+.3f} bps/s (was bearish)",
            }

        return None
