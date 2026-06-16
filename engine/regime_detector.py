# engine/regime_detector.py

import time
import numpy as np


class RegimeDetector:
    def __init__(self):
        self.baseline_vol = 0.001  # Tunable baseline volatility
        self.last_regime = "RANGE"
        self.last_update = 0
        self.shock_cooldown_until = 0

    def update(self, spot_history):
        """
        Classify current market regime from spot price history.

        spot_history: list of (timestamp, price)
        returns dict with:
            regime: "RANGE", "TREND", "HIGH_VOL", or "SHOCK"
            vol_ratio: current vol / baseline (clamped 0.5–3.0)
            trend_strength: cumulative return over lookback
        """
        now = time.time()
        self.last_update = now

        # Default values if not enough data
        if len(spot_history) < 3:
            return {
                "regime": self.last_regime,
                "vol_ratio": 1.0,
                "trend_strength": 0.0,
            }

        # Filter to last 3 minutes of data
        lookback = 3 * 60
        cutoff = now - lookback
        recent = [(t, p) for t, p in spot_history if t >= cutoff]

        if len(recent) < 3:
            return {
                "regime": self.last_regime,
                "vol_ratio": 1.0,
                "trend_strength": 0.0,
            }

        prices = [p for _, p in recent]

        # 1. Compute log returns
        log_returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0 and prices[i] > 0:
                log_returns.append(np.log(prices[i] / prices[i - 1]))

        if len(log_returns) < 2:
            return {
                "regime": self.last_regime,
                "vol_ratio": 1.0,
                "trend_strength": 0.0,
            }

        # 2. Volatility = std of log returns
        volatility = float(np.std(log_returns))

        # 3. Trend strength = cumulative return over the window
        trend_strength = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0

        # 4. Vol ratio (clamped)
        vol_ratio = volatility / self.baseline_vol if self.baseline_vol > 0 else 1.0
        vol_ratio = max(0.5, min(3.0, vol_ratio))

        # 5. Regime classification
        # Check shock cooldown first (sticky)
        if now < self.shock_cooldown_until:
            regime = "SHOCK"
        elif vol_ratio >= 3.0:
            # New shock detected — start cooldown
            self.shock_cooldown_until = now + 90
            regime = "SHOCK"
        elif vol_ratio > 1.8:
            regime = "HIGH_VOL"
        elif abs(trend_strength) > 0.0025 and vol_ratio > 1.2:
            regime = "TREND"
        else:
            regime = "RANGE"

        self.last_regime = regime

        return {
            "regime": regime,
            "vol_ratio": vol_ratio,
            "trend_strength": trend_strength,
        }
