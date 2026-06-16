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
        print(f"[PositionManager] pnl={pnl:.4%} peak={self.peak_pnl:.4%} elapsed={elapsed:.1f}s entry={self.entry_price:.4f} current={current_price:.4f} trend={futures_trend} time_rem={time_remaining} side={self.side}")

        # If early exits are disabled, bypass all exit checks and hold to expiry
        from config import DISABLE_EARLY_EXITS
        if DISABLE_EARLY_EXITS:
            print(f"[PositionManager] HOLD: early exits disabled; letting position settle at expiry")
            return None

        # --- 1. Hard stop loss — uniform for all contracts ---
        if pnl <= STOP_LOSS_PCT:
            print(f"[PositionManager] EXIT: hard stop loss ({pnl:.4%} <= {STOP_LOSS_PCT:.4%})")
            return "EXIT"

        # --- 2. Minimum hold time gate — no further exits before this ---
        if elapsed < MIN_HOLD_TIME_SECONDS:
            print(f"[PositionManager] HOLD: min hold time ({elapsed:.0f}s < {MIN_HOLD_TIME_SECONDS}s)")
            return None

        # --- 3. Late-window resolution logic (time_remaining <= 45s) ---
        # Near expiry: only exit on catastrophic loss with adverse trend.
        # Binary contracts resolve at expiry — let them settle.
        if time_remaining is not None and time_remaining <= 45:
            trend_against = False
            if futures_trend is not None:
                trend_against = (
                    (self.side == "yes" and futures_trend < 0) or
                    (self.side == "no" and futures_trend > 0)
                )
            if pnl < -0.30 and trend_against:
                print(f"[PositionManager] EXIT: late-window catastrophic (pnl={pnl:.4%}, trend={futures_trend}, time_rem={time_remaining})")
                return "EXIT"
            print(f"[PositionManager] HOLD: late-window — letting binary settle at expiry (pnl={pnl:.4%}, time_rem={time_remaining})")
            return None

        # --- 4. Trailing convex profit model ---
        # After reaching 40%+ peak gain, protect by exiting on 15% absolute retrace
        if self.peak_pnl >= 0.40:
            if pnl <= self.peak_pnl - 0.15:
                print(f"[PositionManager] EXIT: convex trailing (peak={self.peak_pnl:.4%}, pnl={pnl:.4%}, retrace={self.peak_pnl - pnl:.4%})")
                return "EXIT"

        # --- 5. Profit protection (50%+ gain) ---
        # Only exit if momentum turns against AND under 3 minutes remain.
        # Otherwise let the winner run toward full binary payout.
        if pnl >= PROFIT_PROTECTION_TRIGGER:
            momentum_favorable = False
            if self.side == "yes" and futures_trend is not None:
                momentum_favorable = futures_trend > 0
            elif self.side == "no" and futures_trend is not None:
                momentum_favorable = futures_trend < 0

            if not momentum_favorable and time_remaining is not None and time_remaining < 180:
                print(f"[PositionManager] EXIT: profit protection (pnl={pnl:.4%}, time_rem={time_remaining}, trend={futures_trend})")
                return "EXIT"
            else:
                print(f"[PositionManager] HOLD: profit {pnl:.4%} — allowing winner to run (trend_favorable={momentum_favorable})")
                return None

        print(f"[PositionManager] HOLD: no exit condition met")
        return None
