ought """
Orderbook Ratio Momentum Drift (ORMD) Strategy

Core Insight: Track the ratio of YES-bid-depth to NO-bid-depth over rolling windows.
This ratio trend predicts directional conviction before price moves.

Entry Logic:
- YES/NO bid-depth ratio increases >20% over 30s → ENTER_YES
- YES/NO bid-depth ratio decreases >20% over 30s → ENTER_NO

Why Novel: Uses bid-depth ratio trend as leading indicator vs current L2 imbalance
which is an absolute snapshot. The ratio momentum captures order flow conviction
before it manifests in price movement.
"""

import time
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field
from collections import deque


@dataclass
class ORMDConfig:
    """Configuration for ORMD strategy"""
    # Ratio change threshold (20% = 0.20)
    RATIO_CHANGE_THRESHOLD: float = 0.20
    # Rolling window for ratio calculation (seconds)
    RATIO_WINDOW_SECONDS: int = 30
    # Minimum time in window before triggering (seconds)
    MIN_WINDOW_TIME: int = 15
    # Entry price bounds
    MIN_ENTRY_PRICE: float = 0.15
    MAX_ENTRY_PRICE: float = 0.50
    # Exit thresholds
    RATIO_REVERT_THRESHOLD: float = 0.10  # Exit when ratio reverts by 10%
    PRICE_CROSS_MID: float = 0.50  # Exit when price crosses 0.50
    # Time-based exit (seconds before expiry)
    TIME_EXIT_THRESHOLD: int = 120
    # Sizing multiplier (standard = 1.0, panic-fade large = 2.0)
    SIZING_MULTIPLIER: float = 1.0


@dataclass
class ORMDState:
    """Runtime state for ORMD strategy"""
    ratio_history: deque = field(default_factory=lambda: deque(maxlen=60))
    ratio_timestamps: deque = field(default_factory=lambda: deque(maxlen=60))
    last_ratio: float = 1.0
    ratio_baseline: float = 1.0
    signal_active: bool = False
    signal_direction: Optional[str] = None
    signal_time: float = 0.0
    entry_price: Optional[float] = None
    entry_ratio: Optional[float] = None


class OrderbookRatioDrift:
    """
    Orderbook Ratio Momentum Drift Strategy
    
    Tracks YES-bid-depth / NO-bid-depth ratio momentum to predict
    directional conviction before price moves.
    """
    
    def __init__(self, config: Optional[ORMDConfig] = None):
        self.config = config or ORMDConfig()
        self.state = ORMDState()
        self._reset_state()
    
    def _reset_state(self):
        """Reset runtime state"""
        self.state = ORMDState()
    
    def update_ratio(self, yes_bid_depth: float, no_bid_depth: float, timestamp: float) -> None:
        """
        Update ratio history with new depth data
        
        Args:
            yes_bid_depth: Total YES bid depth (sum of bid sizes)
            no_bid_depth: Total NO bid depth (sum of bid sizes)
            timestamp: Current timestamp
        """
        if yes_bid_depth <= 0 or no_bid_depth <= 0:
            return
        
        ratio = yes_bid_depth / no_bid_depth
        self.state.ratio_history.append(ratio)
        self.state.ratio_timestamps.append(timestamp)
        self.state.last_ratio = ratio
        
        # Set baseline on first valid data
        if self.state.ratio_baseline == 1.0 and len(self.state.ratio_history) >= 5:
            self.state.ratio_baseline = ratio
    
    def evaluate(
        self,
        kalshi_yes_price: float,
        seconds_remaining: int,
        regime: str,
        current_yes_bid_depth: float,
        current_no_bid_depth: float,
        timestamp: float
    ) -> Optional[Dict]:
        """
        Evaluate ORMD signal
        
        Args:
            kalshi_yes_price: Current Kalshi YES contract price
            seconds_remaining: Seconds until market resolution
            regime: Current market regime (CALM, TREND, HIGH_VOL, SHOCK)
            current_yes_bid_depth: Current YES bid depth
            current_no_bid_depth: Current NO bid depth
            timestamp: Current timestamp
            
        Returns:
            Signal dict if triggered, None otherwise
        """
        # Update ratio history
        self.update_ratio(current_yes_bid_depth, current_no_bid_depth, timestamp)
        
        # Block in SHOCK regime
        if regime == "SHOCK":
            return None
        
        # Block if not enough history
        if len(self.state.ratio_history) < 10:
            return None
        
        # Calculate ratio change from baseline
        ratio_change = (self.state.last_ratio - self.state.ratio_baseline) / self.state.ratio_baseline
        
        # Calculate ratio momentum (rate of change)
        ratio_momentum = self._calculate_ratio_momentum()
        
        # Check entry conditions
        if not self.state.signal_active:
            # Check for new signal
            if self._check_entry_conditions(ratio_change, ratio_momentum, kalshi_yes_price, seconds_remaining):
                self.state.signal_active = True
                self.state.signal_direction = "ENTER_YES" if ratio_change > 0 else "ENTER_NO"
                self.state.signal_time = timestamp
                self.state.entry_price = kalshi_yes_price
                self.state.entry_ratio = self.state.last_ratio
                
                return {
                    "strategy": "ORMD",
                    "direction": self.state.signal_direction,
                    "confidence": self._calculate_confidence(ratio_change, ratio_momentum),
                    "entry_price": kalshi_yes_price,
                    "ratio_change": ratio_change,
                    "ratio_momentum": ratio_momentum,
                    "reason": f"ORMD ratio {'up' if ratio_change > 0 else 'down'} {abs(ratio_change)*100:.1f}% from baseline"
                }
        else:
            # Check for exit conditions
            exit_signal = self._check_exit_conditions(kalshi_yes_price, seconds_remaining, ratio_change)
            if exit_signal:
                self._reset_state()
                return exit_signal
        
        return None
    
    def _calculate_ratio_momentum(self) -> float:
        """
        Calculate rate of ratio change (momentum)
        
        Returns:
            Ratio momentum in bps/sec
        """
        if len(self.state.ratio_history) < 10:
            return 0.0
        
        # Compare recent average to older average
        recent = list(self.state.ratio_history)[-5:]
        older = list(self.state.ratio_history)[-10:-5]
        
        if not older or not recent:
            return 0.0
        
        avg_recent = sum(recent) / len(recent)
        avg_older = sum(older) / len(older)
        
        if avg_older == 0:
            return 0.0
        
        # Convert to basis points per second
        ratio_change = (avg_recent - avg_older) / avg_older
        time_delta = len(recent) * 5  # Assuming 5-second ticks
        
        if time_delta == 0:
            return 0.0
        
        return (ratio_change * 10000) / time_delta  # bps/sec
    
    def _check_entry_conditions(
        self,
        ratio_change: float,
        ratio_momentum: float,
        kalshi_yes_price: float,
        seconds_remaining: int
    ) -> bool:
        """
        Check if entry conditions are met
        
        Returns:
            True if entry signal should trigger
        """
        # Check price bounds
        if kalshi_yes_price < self.config.MIN_ENTRY_PRICE or kalshi_yes_price > self.config.MAX_ENTRY_PRICE:
            return False
        
        # Check time remaining
        if seconds_remaining < self.config.TIME_EXIT_THRESHOLD:
            return False
        
        # Check ratio change threshold
        if abs(ratio_change) < self.config.RATIO_CHANGE_THRESHOLD:
            return False
        
        # Check ratio momentum direction matches ratio change
        if ratio_change > 0 and ratio_momentum < 0:
            return False
        if ratio_change < 0 and ratio_momentum > 0:
            return False
        
        return True
    
    def _calculate_confidence(self, ratio_change: float, ratio_momentum: float) -> float:
        """
        Calculate signal confidence
        
        Returns:
            Confidence score 0.0 to 1.0
        """
        # Base confidence from ratio change magnitude
        change_confidence = min(abs(ratio_change) / self.config.RATIO_CHANGE_THRESHOLD, 1.0)
        
        # Momentum confirmation
        momentum_confidence = min(abs(ratio_momentum) / 5.0, 1.0)  # 5 bps/sec = max confidence
        
        # Combined confidence
        confidence = (change_confidence * 0.6) + (momentum_confidence * 0.4)
        
        return min(confidence, 0.95)  # Cap at 95%
    
    def _check_exit_conditions(
        self,
        kalshi_yes_price: float,
        seconds_remaining: int,
        current_ratio_change: float
    ) -> Optional[Dict]:
        """
        Check if exit conditions are met
        
        Returns:
            Exit signal dict if should exit, None otherwise
        """
        # Time-based exit
        if seconds_remaining < self.config.TIME_EXIT_THRESHOLD:
            return {
                "strategy": "ORMD",
                "direction": "EXIT",
                "confidence": 0.8,
                "reason": f"Time exit: {seconds_remaining}s remaining"
            }
        
        # Price crosses mid
        if self.state.signal_direction == "ENTER_YES" and kalshi_yes_price > self.config.PRICE_CROSS_MID:
            return {
                "strategy": "ORMD",
                "direction": "EXIT",
                "confidence": 0.9,
                "reason": f"Price crossed {self.config.PRICE_CROSS_MID}"
            }
        if self.state.signal_direction == "ENTER_NO" and kalshi_yes_price < self.config.PRICE_CROSS_MID:
            return {
                "strategy": "ORMD",
                "direction": "EXIT",
                "confidence": 0.9,
                "reason": f"Price crossed {self.config.PRICE_CROSS_MID}"
            }
        
        # Ratio reverts
        if self.state.signal_direction == "ENTER_YES" and current_ratio_change < -self.config.RATIO_REVERT_THRESHOLD:
            return {
                "strategy": "ORMD",
                "direction": "EXIT",
                "confidence": 0.7,
                "reason": f"Ratio reverted {current_ratio_change*100:.1f}%"
            }
        if self.state.signal_direction == "ENTER_NO" and current_ratio_change > self.config.RATIO_REVERT_THRESHOLD:
            return {
                "strategy": "ORMD",
                "direction": "EXIT",
                "confidence": 0.7,
                "reason": f"Ratio reverted {current_ratio_change*100:.1f}%"
            }
        
        return None
    
    def get_position_size(self, base_size: float) -> float:
        """
        Calculate position size based on strategy configuration
        
        Args:
            base_size: Base position size from Kelly sizer
            
        Returns:
            Adjusted position size
        """
        return base_size * self.config.SIZING_MULTIPLIER
    
    def reset(self):
        """Reset strategy state"""
        self._reset_state()


# Singleton instance for cross-asset coordination
_ormd_instance: Optional[OrderbookRatioDrift] = None


def get_ormd_instance(config: Optional[ORMDConfig] = None) -> OrderbookRatioDrift:
    """Get singleton ORMD instance"""
    global _ormd_instance
    if _ormd_instance is None:
        _ormd_instance = OrderbookRatioDrift(config)
    return _ormd_instance