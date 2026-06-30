"""
Window-Opening Vacuum Fade (WOVF) Strategy

Core Insight: At window open (:00/:15/:30/:45), books are thinnest and price 
discovery is most volatile. First 15-second moves often overshoot before stabilizing.

Entry Logic:
- In first 20s of window, if Kalshi YES moves >3% from open → ENTER_NO (fade the vacuum spike)
- In first 20s of window, if Kalshi YES drops >3% from open → ENTER_YES (fade the vacuum drop)

Why Novel: Different from panic_fade which triggers mid-window on sustained moves.
WOVF exploits the specific thin-book opening volatility pattern unique to the 
predictable 15-minute window schedule.

Sizing: Large (panic-fade conviction sizing per TurbineFi finding)
Exit: Price stabilizes (velocity <0.5 bps/sec), or crosses back through open±1%, 
      or <2 min to expiry
"""

import time
from typing import Optional, Dict
from dataclasses import dataclass, field
from collections import deque


@dataclass
class WOVFConfig:
    """Configuration for WOVF strategy"""
    # Window open detection window (seconds)
    WINDOW_OPEN_WINDOW: int = 20
    # Price move threshold from open (3% = 0.03)
    OPEN_MOVE_THRESHOLD: float = 0.03
    # Exit when price crosses back through open ± this amount
    CROSSBACK_THRESHOLD: float = 0.01
    # Velocity threshold for stabilization (bps/sec)
    STABILIZE_VELOCITY: float = 0.5
    # Entry price bounds
    MIN_ENTRY_PRICE: float = 0.15
    MAX_ENTRY_PRICE: float = 0.50
    # Time-based exit (seconds before expiry)
    TIME_EXIT_THRESHOLD: int = 120
    # Sizing multiplier (large = panic-fade conviction)
    SIZING_MULTIPLIER: float = 2.0


@dataclass
class WOVFState:
    """Runtime state for WOVF strategy"""
    window_open_price: float = 0.0
    window_open_time: float = 0.0
    is_window_open: bool = False
    signal_active: bool = False
    signal_direction: Optional[str] = None
    signal_time: float = 0.0
    entry_price: Optional[float] = None
    price_history: deque = field(default_factory=lambda: deque(maxlen=30))
    velocity_history: deque = field(default_factory=lambda: deque(maxlen=10))


class WindowVacuumFade:
    """
    Window-Opening Vacuum Fade Strategy
    
    Exploits the thin-book opening volatility at the start of each 15-minute window.
    First 15-20 second moves often overshoot before stabilizing.
    """
    
    def __init__(self, config: Optional[WOVFConfig] = None):
        self.config = config or WOVFConfig()
        self.state = WOVFState()
        self._reset_state()
    
    def _reset_state(self):
        """Reset runtime state"""
        self.state = WOVFState()
    
    def detect_window_open(self, timestamp: float) -> bool:
        """
        Detect if we're in the window open period
        
        Args:
            timestamp: Current timestamp
            
        Returns:
            True if within WINDOW_OPEN_WINDOW seconds of a window open
        """
        # Window opens at :00, :15, :30, :45
        minute = (timestamp // 60) % 60
        second = timestamp % 60
        
        # Calculate seconds since last window open
        window_minute = (minute // 15) * 15
        seconds_since_open = (minute - window_minute) * 60 + second
        
        return seconds_since_open < self.config.WINDOW_OPEN_WINDOW
    
    def update_price(self, kalshi_yes_price: float, timestamp: float) -> None:
        """
        Update price history
        
        Args:
            kalshi_yes_price: Current Kalshi YES price
            timestamp: Current timestamp
        """
        self.state.price_history.append((kalshi_yes_price, timestamp))
        
        # Calculate velocity
        if len(self.state.price_history) >= 2:
            price_curr, time_curr = self.state.price_history[-1]
            price_prev, time_prev = self.state.price_history[-2]
            
            if time_curr > time_prev:
                price_change = price_curr - price_prev
                price_change_bps = (price_change / price_prev) * 10000
                velocity = price_change_bps / (time_curr - time_prev)
                self.state.velocity_history.append(velocity)
    
    def evaluate(
        self,
        kalshi_yes_price: float,
        seconds_remaining: int,
        regime: str,
        timestamp: float
    ) -> Optional[Dict]:
        """
        Evaluate WOVF signal
        
        Args:
            kalshi_yes_price: Current Kalshi YES contract price
            seconds_remaining: Seconds until market resolution
            regime: Current market regime (CALM, TREND, HIGH_VOL, SHOCK)
            timestamp: Current timestamp
            
        Returns:
            Signal dict if triggered, None otherwise
        """
        # Update price history
        self.update_price(kalshi_yes_price, timestamp)
        
        # Detect window open
        is_window_open = self.detect_window_open(timestamp)
        
        # Initialize window open price on first tick of window
        if is_window_open and not self.state.is_window_open:
            self.state.is_window_open = True
            self.state.window_open_price = kalshi_yes_price
            self.state.window_open_time = timestamp
        
        # Block in SHOCK regime
        if regime == "SHOCK":
            return None
        
        # Check entry conditions
        if not self.state.signal_active:
            if self._check_entry_conditions(kalshi_yes_price, seconds_remaining, is_window_open):
                self.state.signal_active = True
                self.state.signal_direction = "ENTER_NO" if kalshi_yes_price > self.state.window_open_price else "ENTER_YES"
                self.state.signal_time = timestamp
                self.state.entry_price = kalshi_yes_price
                
                move_pct = abs(kalshi_yes_price - self.state.window_open_price) / self.state.window_open_price
                
                return {
                    "strategy": "WOVF",
                    "direction": self.state.signal_direction,
                    "confidence": self._calculate_confidence(move_pct),
                    "entry_price": kalshi_yes_price,
                    "window_open_price": self.state.window_open_price,
                    "move_from_open": move_pct,
                    "reason": f"WOVF vacuum fade: price moved {move_pct*100:.1f}% from window open"
                }
        else:
            # Check for exit conditions
            exit_signal = self._check_exit_conditions(kalshi_yes_price, seconds_remaining)
            if exit_signal:
                self._reset_state()
                return exit_signal
        
        # Reset window open flag when window open period ends
        if not is_window_open:
            self.state.is_window_open = False
        
        return None
    
    def _check_entry_conditions(
        self,
        kalshi_yes_price: float,
        seconds_remaining: int,
        is_window_open: bool
    ) -> bool:
        """
        Check if entry conditions are met
        
        Returns:
            True if entry signal should trigger
        """
        # Must be in window open period
        if not is_window_open:
            return False
        
        # Must have window open price
        if self.state.window_open_price == 0:
            return False
        
        # Check time remaining
        if seconds_remaining < self.config.TIME_EXIT_THRESHOLD:
            return False
        
        # Calculate move from open
        move_from_open = abs(kalshi_yes_price - self.state.window_open_price) / self.state.window_open_price
        
        # Check move threshold
        if move_from_open < self.config.OPEN_MOVE_THRESHOLD:
            return False
        
        # Check price bounds
        if kalshi_yes_price < self.config.MIN_ENTRY_PRICE or kalshi_yes_price > self.config.MAX_ENTRY_PRICE:
            return False
        
        return True
    
    def _calculate_confidence(self, move_from_open: float) -> float:
        """
        Calculate signal confidence
        
        Returns:
            Confidence score 0.0 to 1.0
        """
        # Base confidence from move magnitude
        base_confidence = min(move_from_open / (self.config.OPEN_MOVE_THRESHOLD * 2), 1.0)
        
        # Cap at 90% (never 100% - always uncertainty)
        return min(base_confidence, 0.90)
    
    def _check_exit_conditions(
        self,
        kalshi_yes_price: float,
        seconds_remaining: int
    ) -> Optional[Dict]:
        """
        Check if exit conditions are met
        
        Returns:
            Exit signal dict if should exit, None otherwise
        """
        # Time-based exit
        if seconds_remaining < self.config.TIME_EXIT_THRESHOLD:
            return {
                "strategy": "WOVF",
                "direction": "EXIT",
                "confidence": 0.8,
                "reason": f"Time exit: {seconds_remaining}s remaining"
            }
        
        # Calculate current move from window open
        if self.state.window_open_price > 0:
            move_from_open = abs(kalshi_yes_price - self.state.window_open_price) / self.state.window_open_price
            
            # Price crosses back through open ± threshold
            if move_from_open < self.config.CROSSBACK_THRESHOLD:
                return {
                    "strategy": "WOVF",
                    "direction": "EXIT",
                    "confidence": 0.9,
                    "reason": f"Price stabilized: moved back within {self.config.CROSSBACK_THRESHOLD*100:.1f}% of open"
                }
        
        # Velocity stabilization
        if len(self.state.velocity_history) >= 3:
            recent_velocity = sum(list(self.state.velocity_history)[-3:]) / 3
