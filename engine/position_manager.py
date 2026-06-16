# engine/position_manager.py

import time
from config import *

class PositionManager:
    def __init__(self):
        self.entry_price = None
        self.best_price = None
        self.contracts = 0
        self.side = None
        self.peak_pnl = 0.0
        self.entry_time = None

    def open(self, price=None, contracts=0, side=None):
        self.entry_price = price
        self.best_price = price
        self.contracts = contracts
        self.side = side
        self.entry_time = time.time()
        self.peak_pnl = 0.0

    def close(self):
        self.entry_price = None
        self.best_price = None
        self.contracts = 0
        self.peak_pnl = 0.0
        self.side = None
        self.entry_time = None

    def _pnl(self, current_price):
        if self.entry_price is None or self.side not in ("yes", "no"):
            return 0.0

        return (current_price - self.entry_price) / self.entry_price

    def update(self, current_price, futures_trend=None, time_remaining=None, move_pct=0):
        if self.entry_price is None:
            self.entry_price = current_price
            self.best_price = current_price
            self.entry_time = time.time()
            print(f"[PositionManager] No entry_price; set to {current_price:.4f}, returning None")
            return None

        if current_price > self.best_price:
            self.best_price = current_price

        pnl = self._pnl(current_price)
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl
        elapsed = time.time() - self.entry_time if self.entry_time else 0
        print(f"[PositionManager] pnl={pnl:.4%} elapsed={elapsed:.1f}s entry={self.entry_price:.4f} current={current_price:.4f} trend={futures_trend} time_rem={time_remaining} move_pct={move_pct:.6f} side={self.side}")

        # If early exits are disabled, bypass all exit checks and hold to expiry
        from config import DISABLE_EARLY_EXITS
        if DISABLE_EARLY_EXITS:
            print(f"[PositionManager] HOLD: early exits disabled; letting position settle at expiry")
            return None

        # Determine price-dependent stop-loss limit
        if self.entry_price <= 0.10:
            active_stop_loss = -1.00  # Effectively no stop loss for cheap premium
        elif self.entry_price <= 0.20:
            active_stop_loss = -0.40  # wider stop loss for medium cheap contracts
        else:
            active_stop_loss = STOP_LOSS_PCT

        # 1. Hard stop loss (price-dependent)
        if pnl <= active_stop_loss:
            print(f"[PositionManager] EXIT: stop loss ({pnl:.4%} <= {active_stop_loss:.4%})")
            return "EXIT"

        # 2. Momentum-reversal exit: down >12% with futures trend against position (requires min hold time)
        if elapsed >= MIN_HOLD_TIME_SECONDS:
            if pnl < -0.12 and futures_trend is not None:
                trend_unfavorable = (
                    (self.side == "yes" and futures_trend < 0) or
                    (self.side == "no" and futures_trend > 0)
                )
                if trend_unfavorable:
                    print(f"[PositionManager] EXIT: momentum reversal (pnl={pnl:.4%}, trend={futures_trend})")
                    return "EXIT"



        # 5. Time-decay profit take: >10% profit with under 3 minutes
        if time_remaining is not None and time_remaining <= 180:
            if pnl > 0.10 and move_pct < 0:
                print(f"[PositionManager] EXIT: time-decay profit take (pnl={pnl:.4%}, time_rem={time_remaining})")
                return "EXIT"

        # 6. Profit protection: exit at 35%+ unless momentum still favorable
        if pnl >= PROFIT_PROTECTION_TRIGGER:
            momentum_favorable = False
            if self.side == "yes" and futures_trend is not None:
                momentum_favorable = futures_trend > 0
            elif self.side == "no" and futures_trend is not None:
                momentum_favorable = futures_trend < 0

            # New condition: only exit if trend is unfavorable AND time remaining is less than 180 seconds
            if not momentum_favorable and time_remaining is not None and time_remaining < 180:
                print(f"[PositionManager] EXIT: profit protection (pnl={pnl:.4%}, time_rem={time_remaining})")
                return "EXIT"
            else:
                print(f"[PositionManager] HOLD: profit protection (pnl={pnl:.4%}) allowing winner to run")
                return None
        # Trailing stop: exit if pnl drops 15% from peak after profit protection
        if self.peak_pnl > 0 and (self.peak_pnl - pnl) >= 0.15 * self.peak_pnl:
            print(f"[PositionManager] EXIT: trailing stop (peak={self.peak_pnl:.4%}, pnl={pnl:.4%})")
            return "EXIT"

        print(f"[PositionManager] HOLD: no exit condition met")
        return None
