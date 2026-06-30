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
        self.position_type = "buy"     # "buy" = long YES, "sell" = short YES (converted NO)

    def open(self, price=None, contracts=0, side=None, position_type="buy", reset_peak=True):
        self.entry_price = price
        self.best_price = price
        self.contracts = contracts
        self.side = side
        self.entry_time = time.time()
        if reset_peak:
            self.peak_pnl = 0.0
        self.position_type = position_type

    def sync_from_portfolio(self, port_pos):
        self.entry_price = float(port_pos.get("entry_price", 0.0))
        self.contracts = int(port_pos.get("count", 0))
        self.side = port_pos.get("side")
        self.entry_time = None

    def close(self):
        self.entry_price = None
        self.best_price = None
        self.contracts = 0
        self.peak_pnl = 0.0
        self.side = None
        self.entry_time = None
        self.position_type = "buy"

    def _pnl(self, current_price):
        if self.entry_price is None or self.side not in ("yes", "no"):
            return 0.0

        return (current_price - self.entry_price) / self.entry_price

    def update(self, current_price, futures_trend=None, time_remaining=None, move_pct=0, regime=None):
        if self.entry_price is None:
            self.entry_price = current_price
            self.best_price = current_price
            self.entry_time = time.time()
            print(f"[PositionManager] No entry_price; set to {current_price:.4f}, returning None")
            return (None, None)

        if current_price > self.best_price:
            self.best_price = current_price

        pnl = self._pnl(current_price)
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl
        elapsed = time.time() - self.entry_time if self.entry_time else 0
        # print(f"[PositionManager] pnl={pnl:.4%} peak={self.peak_pnl:.4%} elapsed={elapsed:.1f}s entry={self.entry_price:.4f} current={current_price:.4f} trend={futures_trend} time_rem={time_remaining} side={self.side} regime={regime}")

        # If early exits are disabled, bypass all exit checks and hold to expiry
        from config import DISABLE_EARLY_EXITS
        if DISABLE_EARLY_EXITS:
            # print(f"[PositionManager] HOLD: early exits disabled; letting position settle at expiry")
            return (None, None)

        # --- LATE WINDOW LOGIC (Takes precedence) ---
        if time_remaining is not None:
            # 1. Winning in the last 3 minutes -> Let it settle!
            if time_remaining <= 180 and pnl > 0:
                # print(f"[PositionManager] HOLD: winning in last 3 mins, letting it settle (pnl={pnl:.4%})")
                return (None, None)
            
            # 2. Losing in the last 1 minute -> Close it out for whatever we can salvage
            # ONLY if there is "no chance" (down 80%+ with trend against us, or price <= $0.05)
            if time_remaining <= 60 and pnl < 0:
                trend_against = False
                if futures_trend is not None:
                    trend_against = (
                        (self.side == "yes" and futures_trend < 0) or
                        (self.side == "no" and futures_trend > 0)
                    )
                if (pnl < -0.80 and trend_against) or current_price <= 0.05:
                    print(f"[PositionManager] EXIT: losing in last 1 min with no chance (pnl={pnl:.4%}, price={current_price:.4f})")
                    return ("EXIT", "salvage_loss")

        # --- Dynamic Stop Loss ---
        # User requested: Give it breathing room (-85%) until the final minute, 
        # then tighten it to the standard stop loss.
        if time_remaining is not None and time_remaining > 60:
            dynamic_stop = -0.85
        else:
            dynamic_stop = STOP_LOSS_PCT

        # --- Catastrophic stop - bypasses minimum hold time ---
        if pnl <= -0.95:
            print(f"[PositionManager] EXIT: catastrophic stop loss (pnl={pnl:.4%})")
            return ("EXIT", "catastrophic_stop")

        # --- Hard stop loss — uniform for all contracts ---
        if pnl <= dynamic_stop:
            print(f"[PositionManager] EXIT: hard stop loss ({pnl:.4%} <= {dynamic_stop:.4%}, regime={regime})")
            return ("EXIT", "hard_stop_loss")

        # --- Minimum hold time gate — no further exits before this ---
        if elapsed < MIN_HOLD_TIME_SECONDS:
            # print(f"[PositionManager] HOLD: min hold time ({elapsed:.0f}s < {MIN_HOLD_TIME_SECONDS}s)")
            return (None, None)

        # --- SHOCK regime: only hard stop fires, let binary settle ---
        if regime == "SHOCK":
            # print(f"[PositionManager] HOLD: SHOCK regime — only hard stop active, letting binary settle")
            return (None, None)

        # --- Trailing Profit Model ---
        # After reaching 75%+ peak gain, trail tightly with a 15% absolute retrace
        # Note: If time_remaining <= 180 and winning, it returns early above. So this only fires >3mins left.
        if self.peak_pnl >= 0.75:
            if pnl <= self.peak_pnl - 0.15:
                print(f"[PositionManager] EXIT: convex trailing (peak={self.peak_pnl:.4%}, pnl={pnl:.4%}, retrace={self.peak_pnl - pnl:.4%})")
                return ("EXIT", "trailing_stop")

        # print(f"[PositionManager] HOLD: no exit condition met")
        return (None, None)

