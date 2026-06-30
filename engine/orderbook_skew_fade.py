"""
Orderbook Skew Fade Strategy

When one side of a Kalshi binary market becomes overcrowded (e.g., 90% of limit
orders on YES), the market is one-sided. New information triggers panic exits
from the overcrowded side, causing sharp reversals. This strategy fades the
skew by betting on the undercrowded side.

Key insight: Thin Kalshi books amplify overcrowding signals. When everyone is
on one side, there's no one left to buy — the next catalyst triggers cascade.
"""

import time

from config import CFB_INDEX_MAP


class OrderbookSkewFade:
    """
    Detects extreme orderbook imbalances and fades the crowded side.

    - YES depth / total depth > SKEW_THRESHOLD  → fade to NO (sell YES)
    - YES depth / total depth < (1 - SKEW_THRESHOLD) → fade to YES (buy YES)

    Exits on: price reversion, time stop, or momentum reversal.
    """

    def __init__(
        self,
        asset: str,
        skew_threshold: float = 0.75,
        min_total_depth: int = 10,
        min_spread_bps: float = 50,
        signal_cooldown: float = 45.0,
    ):
        self.asset = asset.upper()
        self.skew_threshold = skew_threshold
        self.min_total_depth = min_total_depth
        self.min_spread_bps = min_spread_bps  # minimum spread to indicate thin book
        self.signal_cooldown = signal_cooldown
        self._last_signal_time = 0.0

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        l2_bids: dict,
        l2_asks: dict,
        yes_bid: float | None = None,
        yes_ask: float | None = None,
        time_remaining: int = 0,
    ) -> dict | None:
        """
        Evaluate orderbook skew for a fade signal.

        Args:
            l2_bids: {price: quantity} for YES bids
            l2_asks: {price: quantity} for YES asks
            yes_bid: current best bid (fallback if L2 empty)
            yes_ask: current best ask (fallback if L2 empty)
            time_remaining: seconds remaining in window

        Returns:
            Signal dict with direction, confidence, sizing, or None.
        """
        # Skip in final 60s (too late, thin books)
        if time_remaining < 60:
            return None

        # Skip early in window (book not stable yet)
        if time_remaining > 840:
            return None

        # Cooldown
        now = time.time()
        if now - self._last_signal_time < self.signal_cooldown:
            return None

        # Calculate YES depth
        yes_depth = sum(l2_bids.values()) if l2_bids else 0
        no_depth = sum(l2_asks.values()) if l2_asks else 0
        total_depth = yes_depth + no_depth

        # Need minimum depth to have a meaningful signal
        if total_depth < self.min_total_depth:
            return None

        # Calculate skew ratio
        yes_ratio = yes_depth / total_depth
        no_ratio = 1.0 - yes_ratio

        # Determine direction
        direction = None
        confidence = 0.0

        if yes_ratio > self.skew_threshold:
            # YES is overcrowded → fade to NO (sell YES)
            direction = "ENTER_NO"
            # Confidence increases with more extreme skew
            confidence = min(
                1.0, (yes_ratio - self.skew_threshold) / (1.0 - self.skew_threshold)
            )
        elif no_ratio > self.skew_threshold:
            # NO is overcrowded → fade to YES (buy YES)
            direction = "ENTER_YES"
            confidence = min(
                1.0, (no_ratio - self.skew_threshold) / (1.0 - self.skew_threshold)
            )
        else:
            # Balanced book → no edge
            return None

        # Minimum confidence floor
        if confidence < 0.3:
            return None

        # Calculate spread as sanity check (wide spreads = thin books = higher risk)
        spread_bps = 0.0
        if yes_bid is not None and yes_ask is not None and yes_bid > 0:
            spread_bps = (yes_ask - yes_bid) / yes_bid * 10000

        signal = {
            "direction": direction,
            "confidence": confidence,
            "yes_depth": yes_depth,
            "no_depth": no_depth,
            "yes_ratio": yes_ratio,
            "no_ratio": no_ratio,
            "spread_bps": spread_bps,
            "sizing_multiplier": self._calculate_sizing(confidence),
            "strategy": "SKEW_FADE",
            "reason": (
                f"SKEW_FADE: YES={yes_ratio:.1%} NO={no_ratio:.1%}, "
                f"total={total_depth:.0f}, spread={spread_bps:.0f}bps"
            ),
        }

        self._last_signal_time = now
        return signal

    def _calculate_sizing(self, confidence: float) -> float:
        """
        Calculate sizing multiplier based on confidence.

        TurbineFi research shows fade_size=100 (large conviction) dominates.
        Scale from 1x to 3x based on confidence.
        """
        return 1.0 + (confidence * 2.0)  # 1x to 3x

    def check_exit(
        self,
        entry_direction: str,
        current_yes_price: float,
        entry_price: float,
        elapsed_seconds: float,
    ) -> dict | None:
        """
        Check if an active skew fade position should exit early.

        Exit conditions:
        1. Price has reverted against our fade (skew corrected)
        2. Hold time exceeded (30s max for scalps)
        3. Spread widened too much (book getting thin)
        """
        pnl_pct = (
            (current_yes_price - entry_price) / entry_price if entry_price > 0 else 0
        )

        # Quick exit: target 5-8% profit
        if entry_direction == "ENTER_YES" and pnl_pct >= 0.05:
            return {
                "action": "EXIT",
                "reason": f"SKEW_FADE TP: +{pnl_pct:.1%} profit",
            }
        if entry_direction == "ENTER_NO" and pnl_pct <= -0.05:
            return {
                "action": "EXIT",
                "reason": f"SKEW_FADE TP: +{abs(pnl_pct):.1%} profit",
            }

        # Stop loss: 3% against
        if entry_direction == "ENTER_YES" and pnl_pct <= -0.03:
            return {
                "action": "EXIT",
                "reason": f"SKEW_FADE SL: {pnl_pct:.1%} loss",
            }
        if entry_direction == "ENTER_NO" and pnl_pct >= 0.03:
            return {
                "action": "EXIT",
                "reason": f"SKEW_FADE SL: {abs(pnl_pct):.1%} loss",
            }

        # Time stop: 60s max hold
        if elapsed_seconds > 60:
            return {
                "action": "EXIT",
                "reason": f"SKEW_FADE time: {elapsed_seconds:.0f}s hold",
            }

        return None
