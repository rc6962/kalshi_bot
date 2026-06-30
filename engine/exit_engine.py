import time
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_PCT, ASSET_STOP_LOSS_OVERRIDE


class ExitEngine:
    def __init__(self):
        self.entry_price = None
        self.contracts = 0
        self.side = None
        self.peak_pnl = 0.0
        self.entry_time = None
        self.position_type = "buy"       # "buy" = long YES, "sell" = short YES (converted NO)
        self.asset = None
        self._last_logged_exit = {}      # {exit_reason: time} — debounce log spam

    def sync_position(self, entry_price, contracts, side, entry_time, peak_pnl, position_type="buy", asset=None):
        is_new = (self.asset != asset or self.entry_time != entry_time)
        self.entry_price = entry_price
        self.contracts = contracts
        self.side = side
        self.entry_time = entry_time
        self.peak_pnl = peak_pnl
        self.position_type = position_type
        self.asset = asset
        if is_new:
            self._time_stop_triggered = False
            self._last_logged_exit.clear()

    def _pnl(self, current_price):
        if self.entry_price is None or self.entry_price <= 0:
            return 0.0
        raw = (current_price - self.entry_price) / self.entry_price
        return raw if self.position_type == "buy" else -raw

    def _log_once(self, reason, message):
        now = time.time()
        last = self._last_logged_exit.get(reason, 0)
        if now - last < 5:
            return
        self._last_logged_exit[reason] = now
        print(message)

    def evaluate(self, current_price, current_bid, current_ask, liquidity_depth, time_remaining=None, regime=None):
        if self.entry_price is None:
            return (None, None)

        pnl = self._pnl(current_price)
        asset_sl = ASSET_STOP_LOSS_OVERRIDE.get(self.asset, STOP_LOSS_PCT) if self.asset else STOP_LOSS_PCT

        # 0. Deep ITM Take Profit — lock in when the binary is almost surely resolving in our favor
        #    Long YES: contract at $0.80+ means ~80%+ probability of hitting $1.00
        #    Short YES: contract at $0.20- means ~80%+ probability of hitting $0.00
        if self.position_type == "buy" and current_price >= 0.80:
            self._log_once("deep_itm_long", f"[ExitEngine] EXIT: deep_itm_long (price=${current_price:.2f}, locking in near-max profit)")
            return ("EXIT", "deep_itm")
        if self.position_type == "sell" and current_price <= 0.20:
            self._log_once("deep_itm_short", f"[ExitEngine] EXIT: deep_itm_short (price=${current_price:.2f}, locking in near-max profit)")
            return ("EXIT", "deep_itm")

        # 1. Take Profit — exit when PnL reaches target
        if pnl >= TAKE_PROFIT_PCT:
            if current_price > 0.01:
                self._log_once("take_profit", f"[ExitEngine] EXIT: take_profit (pnl={pnl:.4%}, target={TAKE_PROFIT_PCT:.2%})")
                return ("EXIT", "take_profit")

        # 2. Time-dependent Stop Loss — tighten as expiry approaches to account for theta decay
        if time_remaining is not None:
            if time_remaining > 300:
                dynamic_sl = asset_sl
            elif time_remaining > 120:
                dynamic_sl = max(asset_sl, -0.30)
            else:
                dynamic_sl = max(asset_sl, -0.20)
        else:
            dynamic_sl = asset_sl

        if pnl <= dynamic_sl:
            if current_price > 0.01:
                self._log_once("hard_stop", f"[ExitEngine] EXIT: hard_stop (pnl={pnl:.4%}, threshold={dynamic_sl:.2%}, time_remaining={time_remaining}s)")
                return ("EXIT", "hard_stop")

        # 3. Trailing Stop — protect gains once position shows >15% peak
        #    12% drawdown is wide enough to survive rapid swings but tight enough to protect gains
        TRAIL_ACTIVATION = 0.20
        if self.peak_pnl >= TRAIL_ACTIVATION:
            drawdown = self.peak_pnl - pnl
            if drawdown >= TRAILING_STOP_PCT:
                self._log_once("trailing_stop", f"[ExitEngine] EXIT: trailing_stop (peak={self.peak_pnl:.4%}, pnl={pnl:.4%}, dd={drawdown:.4%})")
                return ("EXIT", "trailing_stop")

        # 4. End-of-Window Dump — last 60 seconds, exit at whatever price is available
        if time_remaining is not None and time_remaining <= 60:
            self._log_once("end_of_window", f"[ExitEngine] EXIT: end_of_window (time_remaining={time_remaining}s)")
            return ("EXIT", "end_of_window")

        return (None, None)
