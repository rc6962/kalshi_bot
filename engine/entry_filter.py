# engine/entry_filter.py
"""
Entry Filter - pure decision logic, no API calls.

Pure helper functions for:
  - parse_asset_from_ticker
  - parse_window_from_ticker
  - resolve_contract_threshold
  - compute_distance_bps
  - compute_momentum_bps
  - evaluate_entry_filter
  - compute_allowed_size

Plus a ContractStateTracker for per-contract reentry and cooldown limits.
"""

import time
import json
import re
import logging
from datetime import datetime, timezone

import config as cfg

logger = logging.getLogger("entry_filter")

# ---------------------------------------------------------------------------
# Asset / Ticker Parsing
# ---------------------------------------------------------------------------

# KXBTC15M-26JUN151715-B65000   or   KXETH15M-26JUN151900
_ASSET_RE = re.compile(r"^KX([A-Z]+)15M-", re.IGNORECASE)


def parse_asset_from_ticker(ticker: str) -> str | None:
    """Extract the asset symbol from a Kalshi 15-minute crypto ticker."""
    if not ticker:
        return None
    m = _ASSET_RE.match(ticker)
    return m.group(1).upper() if m else None


def parse_window_from_ticker(ticker: str) -> tuple[int | None, int | None]:
    """
    Return (window_start_unix, window_end_unix) from a Kalshi 15-minute ticker.
    Example ticker part:  26JUN151715  => 15:17 UTC, window ends 15:32 UTC.
    Returns (None, None) on failure.
    """
    try:
        date_part = ticker.split("-", 1)[1]  # e.g. "26JUN151715"
        day = int(date_part[:2])
        month_str = date_part[2:5].upper()
        hour = int(date_part[5:7])
        minute = int(date_part[7:9])

        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
            "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
            "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        month = month_map.get(month_str)
        if month is None:
            return None, None

        year = datetime.now(timezone.utc).year
        import calendar as _cal
        expiry_struct = time.struct_time((year, month, day, hour, minute, 0, 0, 0, 0))
        window_end = _cal.timegm(expiry_struct)
        window_start = window_end - 15 * 60
        return window_start, window_end
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Threshold Resolution
# ---------------------------------------------------------------------------

def resolve_contract_threshold(market_meta: dict | None, ticker: str) -> float | None:
    """
    Prefer clean metadata field, fall back to numeric suffix in ticker.
    Returns the threshold price as a float, or None if not determinable.
    """
    if market_meta:
        for key in ("strike_price", "strike", "threshold", "cap_strike",
                    "floor_strike", "yes_sub_title", "sub_title", "subtitle"):
            val = market_meta.get(key)
            if val is not None:
                try:
                    f = float(str(val).replace(",", ""))
                    if f > 0:
                        return f
                except (ValueError, TypeError):
                    pass

    # Ticker suffix e.g. KXBTC15M-26JUN151715-B65000  → 65000
    suffix_match = re.search(r"-[BT](\d+(?:\.\d+)?)$", ticker or "")
    if suffix_match:
        try:
            return float(suffix_match.group(1))
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Distance / Momentum
# ---------------------------------------------------------------------------

def compute_distance_bps(spot: float, threshold: float) -> float:
    """Return |spot - threshold| / spot * 10000 (basis points)."""
    if spot <= 0:
        return float("inf")
    return abs(spot - threshold) / spot * 10_000


def compute_momentum_bps(price_history: list[tuple[float, float]], lookback_seconds: float) -> float:
    """
    Compute momentum in basis-points from price history.

    price_history: list of (unix_timestamp, price) pairs, oldest first.
    lookback_seconds: how far back to look.

    Returns (current_price - past_price) / past_price * 10000, or 0.0 if not enough data.
    """
    if len(price_history) < 2:
        return 0.0

    now_ts, current_price = price_history[-1]
    cutoff = now_ts - lookback_seconds

    # Walk backwards to find the oldest price within lookback window
    past_price = None
    for ts, px in price_history:
        if ts >= cutoff:
            past_price = px
            break

    if past_price is None or past_price <= 0:
        return 0.0

    return (current_price - past_price) / past_price * 10_000


# ---------------------------------------------------------------------------
# Side Inference
# ---------------------------------------------------------------------------

def infer_side(spot: float, threshold: float, momentum_bps: float,
               time_remaining_sec: float | None = None,
               mode: str = "mean_reversion") -> str | None:
    """
    Infer YES / NO based on spot vs threshold and momentum direction.

    mode='continuation'  — trade with momentum:
        spot > threshold AND momentum rising  → YES
        spot < threshold AND momentum falling → NO

    mode='mean_reversion' — fade the move (bot's current strategy):
        spot > threshold AND momentum falling → NO  (fade up-spike)
        spot < threshold AND momentum rising  → YES (fade down-spike)

    Returns 'yes', 'no', or None (skip).
    """
    if time_remaining_sec is not None:
        if time_remaining_sec > 300:
            mode = "continuation"
        else:
            mode = "mean_reversion"

    min_mom = cfg.MIN_MOMENTUM_ABS_BPS
    if mode == "continuation":
        if spot > threshold and momentum_bps >= min_mom:
            return "yes"
        if spot < threshold and momentum_bps <= -min_mom:
            return "no"
    else:  # mean_reversion
        if spot > threshold and momentum_bps <= -min_mom:
            return "no"
        if spot < threshold and momentum_bps >= min_mom:
            return "yes"
    return None


# ---------------------------------------------------------------------------
# Contract State Tracker
# ---------------------------------------------------------------------------

class ContractStateTracker:
    """
    Tracks per-contract state for the current bot session.
    Keyed by full Kalshi market ticker (string).
    """

    def __init__(self):
        # {ticker: dict}
        self._state: dict[str, dict] = {}
        # {asset: last_fill_unix}
        self._last_fill: dict[str, float] = {}
        # {ticker: int} total contracts filled per window
        self._window_contracts: dict[str, int] = {}
        # {asset: int} total contracts across all windows
        self._asset_contracts: dict[str, int] = {}

    def get(self, ticker: str) -> dict:
        return self._state.setdefault(ticker, {
            "first_seen_time": None,
            "last_signal_time": None,
            "last_order_time": None,
            "last_fill_time": None,
            "filled_entry_count": 0,
            "total_contracts_filled": 0,
            "last_decision": None,
            "last_skip_reason": None,
        })

    def record_signal(self, ticker: str):
        s = self.get(ticker)
        now = time.time()
        if s["first_seen_time"] is None:
            s["first_seen_time"] = now
        s["last_signal_time"] = now

    def record_order(self, ticker: str):
        s = self.get(ticker)
        s["last_order_time"] = time.time()

    def record_fill(self, ticker: str, asset: str, contracts: int):
        s = self.get(ticker)
        now = time.time()
        s["last_fill_time"] = now
        s["filled_entry_count"] += 1
        s["total_contracts_filled"] += contracts
        self._last_fill[asset] = now
        self._window_contracts[ticker] = self._window_contracts.get(ticker, 0) + contracts
        self._asset_contracts[asset] = self._asset_contracts.get(asset, 0) + contracts

    def last_fill_for_asset(self, asset: str) -> float:
        return self._last_fill.get(asset, 0.0)

    def window_contracts(self, ticker: str) -> int:
        return self._window_contracts.get(ticker, 0)

    def asset_contracts(self, asset: str) -> int:
        return self._asset_contracts.get(asset, 0)

    def clear_ticker(self, ticker: str):
        """Call on market rollover to reset per-window tracking."""
        self._state.pop(ticker, None)
        self._window_contracts.pop(ticker, None)


# ---------------------------------------------------------------------------
# Core Entry Filter
# ---------------------------------------------------------------------------

class EntryFilter:
    """
    Stateless filter that decides whether to allow an entry.
    All state lives in ContractStateTracker (passed in per call).

    Usage:
        ef = EntryFilter()
        result = ef.evaluate(
            ticker="KXBTC15M-26JUN151900",
            asset="BTC",
            spot=67000.0,
            threshold=66500.0,
            momentum_bps=5.2,
            time_remaining_sec=450,
            proposed_contracts=5,
            tracker=my_tracker,
        )
    """

    def evaluate(
        self,
        ticker: str,
        asset: str,
        spot: float | None,
        threshold: float | None,
        momentum_bps: float,
        time_remaining_sec: float,
        proposed_contracts: int,
        tracker: ContractStateTracker,
        market_meta: dict | None = None,
    ) -> dict:
        """
        Returns a result dict:
        {
            "decision": "EXECUTE" | "SKIP",
            "skip_reason": str | None,
            "approved_side": "yes" | "no" | None,
            "approved_contracts": int,
            "distance_bps": float,
            "momentum_bps": float,
        }
        """
        result = {
            "decision": "SKIP",
            "skip_reason": None,
            "approved_side": None,
            "approved_contracts": 0,
            "distance_bps": float("inf"),
            "momentum_bps": momentum_bps,
        }

        # 1. Spot symbol mapped?
        if asset not in cfg.SPOT_SYMBOL_MAP:
            result["skip_reason"] = "MISSING_SPOT_SYMBOL_MAPPING"
            return result

        # 2. Spot data present?
        if spot is None or spot <= 0:
            result["skip_reason"] = "STALE_OR_MISSING_SPOT_DATA"
            return result

        # 3. Threshold resolvable?
        if threshold is None:
            threshold = resolve_contract_threshold(market_meta, ticker)
        if threshold is None or threshold <= 0:
            result["skip_reason"] = "MISSING_CONTRACT_THRESHOLD"
            return result

        # 4. Distance filter
        distance_bps = compute_distance_bps(spot, threshold)
        result["distance_bps"] = distance_bps
        if distance_bps > cfg.MAX_DISTANCE_BPS:
            result["skip_reason"] = f"DISTANCE_TOO_LARGE:{distance_bps:.1f}bps"
            return result

        # 5. Time remaining
        if time_remaining_sec < cfg.NO_ENTRY_LAST_SECONDS:
            result["skip_reason"] = f"TOO_CLOSE_TO_EXPIRY:{time_remaining_sec:.0f}s"
            return result

        # 6. Reentry limit
        state = tracker.get(ticker)
        if state["filled_entry_count"] >= cfg.MAX_REENTRIES_PER_CONTRACT:
            result["skip_reason"] = f"MAX_REENTRIES_REACHED:{state['filled_entry_count']}"
            return result

        # 7. Cooldown since last fill in same asset
        last_fill_ts = tracker.last_fill_for_asset(asset)
        if last_fill_ts > 0:
            elapsed = time.time() - last_fill_ts
            if elapsed < cfg.COOLDOWN_SECONDS_AFTER_FILL:
                result["skip_reason"] = f"COOLDOWN_ACTIVE:{elapsed:.0f}s_of_{cfg.COOLDOWN_SECONDS_AFTER_FILL}s"
                return result

        # 8. Side inference from momentum
        side = infer_side(spot, threshold, momentum_bps, time_remaining_sec=time_remaining_sec, mode=cfg.SIDE_LOGIC_MODE)
        if side is None:
            result["skip_reason"] = f"MOMENTUM_DOES_NOT_CONFIRM_SIDE:mom={momentum_bps:.2f}bps"
            return result

        # 9. Window exposure limit
        window_used = tracker.window_contracts(ticker)
        window_remaining = cfg.MAX_CONTRACTS_PER_15M_WINDOW - window_used
        if window_remaining <= 0:
            result["skip_reason"] = f"WINDOW_EXPOSURE_LIMIT:{window_used}contracts"
            return result

        # 10. Asset exposure limit
        asset_used = tracker.asset_contracts(asset)
        asset_remaining = cfg.MAX_CONTRACTS_PER_ASSET - asset_used
        if asset_remaining <= 0:
            result["skip_reason"] = f"ASSET_EXPOSURE_LIMIT:{asset_used}contracts"
            return result

        # 11. Order size cap
        allowed = min(
            proposed_contracts,
            cfg.MAX_CONTRACTS_PER_TRADE,
            window_remaining,
            asset_remaining,
        )
        if allowed <= 0:
            result["skip_reason"] = "ALLOWED_SIZE_ZERO"
            return result

        # All checks passed
        result["decision"] = "EXECUTE"
        result["approved_side"] = side
        result["approved_contracts"] = allowed
        return result


# ---------------------------------------------------------------------------
# Structured Decision Logger
# ---------------------------------------------------------------------------

_decision_logger = logging.getLogger("trade_decisions")


def log_decision(
    ticker: str,
    asset: str,
    spot: float | None,
    threshold: float | None,
    distance_bps: float,
    momentum_bps: float,
    proposed_side: str | None,
    allowed_size: int,
    result: dict,
    window_start: int | None,
    window_end: int | None,
):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "market_ticker": ticker,
        "spot": spot,
        "threshold": threshold,
        "distance_bps": round(distance_bps, 2),
        "momentum_bps": round(momentum_bps, 2),
        "side_logic_mode": cfg.SIDE_LOGIC_MODE,
        "proposed_side": proposed_side,
        "allowed_size": allowed_size,
        "decision": result["decision"],
        "skip_reason": result.get("skip_reason"),
        "contracts_requested": allowed_size,
        "contracts_approved": result.get("approved_contracts", 0),
        "window_start": window_start,
        "window_end": window_end,
    }
    _decision_logger.info(json.dumps(record))
    return record
