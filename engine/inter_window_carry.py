"""
Inter-Window Momentum Carry (IWMC) Strategy

Exploits the predictable 15-minute market schedule and cross-window correlation.
The settlement of window N becomes the de facto opening reference for window N+1.
BTC leads altcoins, creating a deterministic information flow across windows.

Key insight: The CFB RTI settlement price of window N becomes the de facto
opening reference for window N+1. BTC leads altcoins. This creates a deterministic
information flow across windows that no strategy exploits.
"""

import asyncio
import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import ASSET_SYMBOLS, ASSETS


@dataclass
class SettlementRecord:
    """Records settlement data for a completed window."""

    asset: str
    window_start: int
    window_end: int
    settlement_price: float
    window_open_price: float
    momentum: float  # (settlement - open) / open
    timestamp: float = field(default_factory=time.time)


@dataclass
class CarryPrediction:
    """Prediction for next window's opening."""

    asset: str
    predicted_open_price: float
    confidence: float
    carry_factor: float
    source_asset: str  # Which asset's momentum carried (e.g., BTC -> ETH)
    timestamp: float = field(default_factory=time.time)


class InterWindowMomentumCarry:
    """
    Inter-Window Momentum Carry (IWMC) Strategy.

    Exploits the predictable 15-minute market schedule and cross-window correlation.
    The settlement of window N becomes the de facto opening reference for window N+1.
    BTC leads altcoins, creating a deterministic information flow across windows.
    """

    def __init__(self, asset: str, all_assets: List[str] = None):
        self.asset = asset.upper()
        self.all_assets = all_assets or ASSETS

        # Settlement history per asset
        self.settlement_history: Dict[str, deque] = {
            a: deque(maxlen=100) for a in self.all_assets
        }

        # Carry factors: how much of source asset's momentum carries to target
        # Calibrated from live data; start with conservative priors
        self.carry_factors: Dict[str, Dict[str, float]] = {
            # source -> {target: factor}
            "BTC": {
                "ETH": 0.6,
                "SOL": 0.4,
                "DOGE": 0.3,
                "XRP": 0.35,
                "BNB": 0.4,
                "HYPE": 0.25,
            },
            "ETH": {"SOL": 0.3, "DOGE": 0.25, "XRP": 0.3, "BNB": 0.35, "HYPE": 0.2},
        }

        # Minimum momentum threshold to consider carry significant
        self.min_momentum_threshold = 0.0005  # 0.05%

        # Maximum age of settlement data to use (seconds)
        self.max_settlement_age = 300  # 5 minutes

        # Prediction cache
        self._last_prediction: Optional[CarryPrediction] = None
        self._prediction_timestamp = 0

        # Calibration tracking
        self.prediction_errors: deque = deque(maxlen=50)

    def record_settlement(
        self,
        asset: str,
        window_start: int,
        window_end: int,
        settlement_price: float,
        window_open_price: float,
    ):
        """Record settlement data for an asset."""
        if settlement_price <= 0 or window_open_price <= 0:
            return

        momentum = (settlement_price - window_open_price) / window_open_price

        record = SettlementRecord(
            asset=asset,
            window_start=window_start,
            window_end=window_end,
            settlement_price=settlement_price,
            window_open_price=window_open_price,
            momentum=momentum,
        )

        self.settlement_history[asset].append(record)
        print(
            f"[IWMC] {asset} settlement recorded: momentum={momentum:.6f}, "
            f"settle=${settlement_price:.2f}, open=${window_open_price:.2f}"
        )
        self._save_history()  # Persist to disk immediately

    def get_latest_momentum(self, asset: str) -> Optional[float]:
        """Get the most recent momentum for an asset."""
        history = self.settlement_history.get(asset, deque())
        if not history:
            return None

        latest = history[-1]
        age = time.time() - latest.timestamp
        if age > self.max_settlement_age:
            return None

        return latest.momentum

    def estimate_volatility(
        self, asset: str, spot_prices: List[float]
    ) -> Optional[float]:
        """
        Estimate 15-minute realized volatility from recent spot prices.

        Uses rolling 1-minute log returns, scaled to 15-minute horizon.
        Requires at least 15 price points (15 minutes of 1-min data).

        Returns σ_15m (15-minute standard deviation in dollar terms).
        """
        if len(spot_prices) < 16:
            return None

        # Calculate log returns
        log_returns = []
        for i in range(1, len(spot_prices)):
            if spot_prices[i] > 0 and spot_prices[i - 1] > 0:
                log_ret = math.log(spot_prices[i] / spot_prices[i - 1])
                log_returns.append(log_ret)

        if len(log_returns) < 15:
            return None

        # Use last 30 returns (30 minutes of data)
        recent_returns = log_returns[-30:]

        # Calculate standard deviation of 1-minute returns
        mean_ret = sum(recent_returns) / len(recent_returns)
        variance = sum((r - mean_ret) ** 2 for r in recent_returns) / len(
            recent_returns
        )
        sigma_1m = math.sqrt(variance)

        # Scale to 15 minutes: σ_15m = σ_1m * sqrt(15)
        sigma_15m = sigma_1m * math.sqrt(15)

        # Convert to dollar terms: σ_$ = current_price * σ_15m
        current_price = spot_prices[-1]
        sigma_dollars = current_price * sigma_15m

        return sigma_dollars

    def normal_cdf(self, x: float) -> float:
        """Standard normal cumulative distribution function."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def calculate_win_probability(
        self, predicted_price: float, strike: float, volatility: float
    ) -> float:
        """
        Calculate P(S_T > K) using normal approximation.

        P(S_T > K) = 1 - Φ((K - μ) / σ)

        Where:
        - μ = predicted_price
        - K = strike
        - σ = volatility (15-minute std dev in dollars)

        Returns probability between 0 and 1.
        """
        if volatility <= 0:
            return 0.5  # No volatility info → 50/50

        z_score = (predicted_price - strike) / volatility
        return self.normal_cdf(z_score)

    def kalshi_fee(self, price: float) -> float:
        """
        Calculate Kalshi fee for a given contract price.

        fee = multiplier × price × (1 - price)
        Crypto multiplier = 0.07

        Peak fee at $0.50: $0.0175
        """
        multiplier = 0.07
        return multiplier * price * (1 - price)

    def get_latest_settlement_price(self, asset: str) -> Optional[float]:
        """Get the most recent settlement price for an asset."""
        history = self.settlement_history.get(asset, deque())
        if not history:
            return None

        latest = history[-1]
        age = time.time() - latest.timestamp
        if age > self.max_settlement_age:
            return None

        return latest.settlement_price

    def _get_history_file(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "data", "iwmc_history.json")

    def _save_history(self):
        """Persist settlement history to disk."""
        history_file = self._get_history_file()
        data = {}
        for asset, hist in self.settlement_history.items():
            data[asset] = [
                {
                    "asset": r.asset,
                    "window_start": r.window_start,
                    "window_end": r.window_end,
                    "settlement_price": r.settlement_price,
                    "window_open_price": r.window_open_price,
                    "momentum": r.momentum,
                    "timestamp": r.timestamp,
                }
                for r in hist
            ]
        try:
            os.makedirs(os.path.dirname(history_file), exist_ok=True)
            with open(history_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[IWMC] Failed to save history: {e}")

    def _load_history(self):
        """Load settlement history from disk on startup."""
        history_file = self._get_history_file()
        if not os.path.exists(history_file):
            return  # Fresh start
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
            for asset, records in data.items():
                if asset not in self.settlement_history:
                    self.settlement_history[asset] = deque(maxlen=100)
                for r in records:
                    try:
                        record = SettlementRecord(
                            asset=r["asset"],
                            window_start=r["window_start"],
                            window_end=r["window_end"],
                            settlement_price=r["settlement_price"],
                            window_open_price=r["window_open_price"],
                            momentum=r["momentum"],
                            timestamp=r.get("timestamp", r["window_end"]),
                        )
                        self.settlement_history[asset].append(record)
                    except Exception:
                        pass  # Skip corrupted records
            count = sum(len(v) for v in self.settlement_history.values())
            print(f"[IWMC] Loaded {count} settlement records from disk")
        except Exception as e:
            print(f"[IWMC] Failed to load history: {e}")

    def predict_next_window_open(
        self, target_asset: str, current_kalshi_price: float
    ) -> Optional[CarryPrediction]:
        """
        Predict the next window's opening price for target_asset based on
        momentum carry from leader assets (primarily BTC).
        """
        if target_asset == "BTC":
            # BTC is the leader; no carry prediction for BTC itself
            return None

        # Find the best source asset with significant momentum
        best_source = None
        best_momentum = 0
        best_carry_factor = 0

        for source_asset, targets in self.carry_factors.items():
            if target_asset not in targets:
                continue

            momentum = self.get_latest_momentum(source_asset)
            if momentum is None:
                continue

            if abs(momentum) < self.min_momentum_threshold:
                continue

            carry_factor = targets[target_asset]
            effective_momentum = momentum * carry_factor

            if abs(effective_momentum) > abs(best_momentum):
                best_momentum = effective_momentum
                best_source = source_asset
                best_carry_factor = carry_factor

        if best_source is None:
            return None

        # Get the latest settlement price of the source asset
        source_settlement = self.get_latest_settlement_price(best_source)
        if source_settlement is None:
            return None

        # Predict next window open: source_settlement * (1 + carry_momentum)
        predicted_open = source_settlement * (1 + best_momentum)

        print(
            f"[IWMC DEBUG] {target_asset}: source_settlement=${source_settlement:.4f}, "
            f"best_momentum={best_momentum:.6f}, predicted_open=${predicted_open:.6f}"
        )

        # Confidence based on momentum magnitude and carry factor
        confidence = min(0.9, abs(best_momentum) * 10 * best_carry_factor)

        prediction = CarryPrediction(
            asset=target_asset,
            predicted_open_price=predicted_open,
            confidence=confidence,
            carry_factor=best_carry_factor,
            source_asset=best_source,
        )

        self._last_prediction = prediction
        self._prediction_timestamp = time.time()

        print(
            f"[IWMC] {target_asset} prediction: source={best_source}, "
            f"source_momentum={self.get_latest_momentum(best_source):.6f}, "
            f"carry_factor={best_carry_factor:.2f}, "
            f"predicted_open=${predicted_open:.2f}, "
            f"confidence={confidence:.2f}"
        )

        return prediction

    def evaluate_entry_signal(
        self,
        target_asset: str,
        current_kalshi_price: float,
        strike_price: float,
        spot_prices: List[float],
        time_remaining: int,
    ) -> Optional[Dict]:
        """
        Evaluate if there's an IWMC entry signal using probability-based framework.

        Framework:
        1. Predict expiry price (μ) using momentum carry
        2. Estimate 15-minute volatility (σ)
        3. Calculate P_win = P(S_T > K) = 1 - Φ((K - μ) / σ)
        4. Signal if P_win > price + fee + buffer

        Args:
            target_asset: Asset symbol (BTC, ETH, etc.)
            current_kalshi_price: YES contract price ($0.01-$0.99)
            strike_price: Window open price (the strike threshold)
            spot_prices: Recent 1-minute spot prices for volatility estimation
            time_remaining: Seconds until expiry

        Returns:
            Signal dict or None
        """
        # Only trade in first 60 seconds of new window
        if time_remaining < 840:
            return None

        # Get prediction from momentum carry model
        prediction = self.predict_next_window_open(target_asset, current_kalshi_price)
        if prediction is None:
            return None

        # Estimate volatility
        volatility = self.estimate_volatility(target_asset, spot_prices)
        if volatility is None or volatility <= 0:
            # Can't estimate volatility → skip
            return None

        # Calculate win probability: P(S_T > K)
        p_win = self.calculate_win_probability(
            predicted_price=prediction.predicted_open_price,
            strike=strike_price,
            volatility=volatility,
        )

        # Calculate fee threshold
        fee = self.kalshi_fee(current_kalshi_price)
        threshold = current_kalshi_price + fee + 0.01  # 1¢ buffer

        print(
            f"[IWMC DEBUG] {target_asset}: P_win={p_win:.3f}, price=${current_kalshi_price:.2f}, "
            f"fee=${fee:.4f}, threshold=${threshold:.3f}, μ=${prediction.predicted_open_price:.2f}, "
            f"K={strike_price:.2f}, σ=${volatility:.4f}"
        )

        # Determine direction and check edge
        if p_win > threshold:
            # YES is underpriced → ENTER_YES
            direction = "ENTER_YES"
            edge = p_win - threshold
        elif p_win < (1 - current_kalshi_price) - fee - 0.01:
            # NO is underpriced (YES overpriced) → ENTER_NO
            direction = "ENTER_NO"
            edge = (1 - current_kalshi_price - fee - 0.01) - p_win
        else:
            # No edge
            return None

        # Confidence based on edge magnitude
        confidence = min(0.9, 0.3 + edge * 5)  # 2¢ edge → 0.4 conf, 10¢ edge → 0.8 conf

        if confidence < 0.3:
            return None

        signal = {
            "direction": direction,
            "confidence": confidence,
            "predicted_expiry": prediction.predicted_open_price,
            "strike": strike_price,
            "volatility": volatility,
            "p_win": p_win,
            "current_price": current_kalshi_price,
            "edge": edge,
            "source_asset": prediction.source_asset,
            "carry_factor": prediction.carry_factor,
            "strategy": "IWMC",
            "reason": f"IWMC: P_win={p_win:.1%} vs price=${current_kalshi_price:.2f}+fee (${edge:.2f} edge)",
        }

        print(
            f"[IWMC] {target_asset} SIGNAL: {direction} | P_win={p_win:.1%} | "
            f"conf={confidence:.2f} | edge=${edge:.3f} | source={prediction.source_asset}"
        )

        return signal

    def record_prediction_outcome(self, target_asset: str, actual_open_price: float):
        """Record actual outcome for calibration."""
        if self._last_prediction is None or self._last_prediction.asset != target_asset:
            return

        predicted = self._last_prediction.predicted_open_price
        error = (actual_open_price - predicted) / predicted
        self.prediction_errors.append(error)

        print(
            f"[IWMC] {target_asset} calibration: predicted=${predicted:.2f}, "
            f"actual=${actual_open_price:.2f}, error={error:.4f}"
        )

        # Auto-calibrate carry factors based on errors
        self._calibrate_carry_factors(target_asset, error)

    def _calibrate_carry_factors(self, target_asset: str, error: float):
        """Adjust carry factors based on prediction errors."""
        if self._last_prediction is None:
            return

        source = self._last_prediction.source_asset
        if (
            source not in self.carry_factors
            or target_asset not in self.carry_factors[source]
        ):
            return

        # If we consistently overpredict (positive error), reduce carry factor
        # If we consistently underpredict (negative error), increase carry factor
        adjustment = -error * 0.1  # 10% of error as adjustment
        old_factor = self.carry_factors[source][target_asset]
        new_factor = max(0.05, min(1.0, old_factor + adjustment))

        if abs(new_factor - old_factor) > 0.01:
            self.carry_factors[source][target_asset] = new_factor
            print(
                f"[IWMC] Calibrated {source}->{target_asset} carry factor: "
                f"{old_factor:.3f} -> {new_factor:.3f} (error={error:.4f})"
            )

    def get_calibration_stats(self) -> Dict:
        """Get calibration statistics."""
        if not self.prediction_errors:
            return {"mean_error": 0, "std_error": 0, "count": 0}

        errors = list(self.prediction_errors)
        mean_err = sum(errors) / len(errors)
        std_err = (sum((e - mean_err) ** 2 for e in errors) / len(errors)) ** 0.5

        return {
            "mean_error": mean_err,
            "std_error": std_err,
            "count": len(errors),
            "carry_factors": self.carry_factors,
        }


# Global instance manager for cross-asset coordination
class IWMCManager:
    """Manages IWMC instances across all assets for cross-asset coordination."""

    def __init__(self, assets: List[str] = None):
        self.assets = assets or ASSETS
        self.instances: Dict[str, InterWindowMomentumCarry] = {}
        self._initialize_instances()
        # Load persisted settlement history on startup so IWMC doesn't need a warmup window
        # Only load on the first instance to avoid duplicate loading; all instances share the same history
        self.instances[next(iter(self.instances))]._load_history()

    def _initialize_instances(self):
        for asset in self.assets:
            self.instances[asset] = InterWindowMomentumCarry(asset, self.assets)

    def record_settlement(
        self,
        asset: str,
        window_start: int,
        window_end: int,
        settlement_price: float,
        window_open_price: float,
    ):
        """Record settlement for all instances (they share history)."""
        for instance in self.instances.values():
            instance.record_settlement(
                asset, window_start, window_end, settlement_price, window_open_price
            )

    def get_signal(
        self,
        asset: str,
        current_kalshi_price: float,
        strike_price: float,
        spot_prices: List[float],
        time_remaining: int,
    ) -> Optional[Dict]:
        """Get IWMC signal for an asset."""
        if asset not in self.instances:
            return None
        return self.instances[asset].evaluate_entry_signal(
            asset, current_kalshi_price, strike_price, spot_prices, time_remaining
        )

    def record_outcome(self, asset: str, actual_open_price: float):
        """Record actual outcome for calibration."""
        if asset in self.instances:
            self.instances[asset].record_prediction_outcome(asset, actual_open_price)

    def get_all_calibration_stats(self) -> Dict:
        """Get calibration stats for all assets."""
        return {
            asset: inst.get_calibration_stats()
            for asset, inst in self.instances.items()
        }
