# engine/signal_engine.py

import os
import csv
import math
import json
import time
import numpy as np
from datetime import datetime, timezone
from config import *
from engine.latency_optimizer import MicroOptimizations
from engine.entry_filter import (
    EntryFilter, ContractStateTracker,
    parse_asset_from_ticker, resolve_contract_threshold,
    compute_distance_bps, compute_momentum_bps,
    log_decision,
)

# ML Confidence threshold (loaded from config.py)
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ml", "kalshi_lgbm_model.txt")
VETO_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ml_veto_log.csv")
REJECTION_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "signal_rejections.jsonl")


class SignalEngine:
    def __init__(self):
        self.price_history = []
        self.max_history = 20
        self.ml_model = None
        self._load_ml_model()
        self._init_veto_log()
        # Per-contract state tracker (shared across all evaluate() calls)
        self.contract_tracker = ContractStateTracker()
        self._entry_filter = EntryFilter()
        # Rolling spot price history for momentum: list of (unix_ts, price)
        self._spot_history: list[tuple[float, float]] = []

    def _load_ml_model(self):
        """Load LightGBM model in native format. Auto-trains if model is missing but training data exists."""
        try:
            import lightgbm as lgb
            
            # Auto-train model if missing but training data exists
            if not os.path.exists(MODEL_PATH):
                print(f"[SignalEngine] No ML model found at {MODEL_PATH}. Checking for training data to auto-train...")
                data_csv = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ml_training_data.csv")
                if os.path.exists(data_csv):
                    try:
                        from ml.train_model import load_training_data, train_model, save_model
                        print("[SignalEngine] Auto-training LightGBM model...")
                        df = load_training_data(data_csv)
                        model = train_model(df)
                        save_model(model, MODEL_PATH)
                        print("[SignalEngine] Auto-training complete.")
                    except Exception as train_err:
                        print(f"[SignalEngine] Auto-training failed: {train_err}")
                else:
                    print(f"[SignalEngine] Training data not found at {data_csv}. Cannot auto-train.")

            if os.path.exists(MODEL_PATH):
                self.ml_model = lgb.Booster(model_file=MODEL_PATH)
                print(f"[SignalEngine] Loaded ML model from {MODEL_PATH}")
            else:
                print(f"[SignalEngine] No ML model found at {MODEL_PATH}. Using rule-based signals only.")
        except ImportError:
            print("[SignalEngine] lightgbm/pandas/scikit-learn not installed. Using rule-based signals only.")
        except Exception as e:
            print(f"[SignalEngine] Failed to load ML model: {e}. Using rule-based signals only.")
            self.ml_model = None

    def _init_veto_log(self):
        """Initialize veto log CSV with headers if it doesn't exist."""
        if not os.path.exists(VETO_LOG_PATH):
            with open(VETO_LOG_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "signal", "ml_probability", "multiplier",
                    "strike_distance_pct", "recent_move_pct", "time_remaining_sec",
                    "futures_trend", "spread_pct", "veto_reason"
                ])

    def _log_veto(self, signal, ml_prob, multiplier, strike_distance_pct,
                  recent_move_pct, time_remaining_sec, futures_trend, spread_pct, reason):
        """Log vetoed signals for audit trail."""
        try:
            with open(VETO_LOG_PATH, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(),
                    signal,
                    round(ml_prob, 4) if ml_prob is not None else "N/A",
                    round(multiplier, 4),
                    round(strike_distance_pct, 4),
                    round(recent_move_pct, 6),
                    int(time_remaining_sec),
                    round(futures_trend, 6),
                    round(spread_pct, 4) if spread_pct is not None else "N/A",
                    reason
                ])
        except Exception:
            pass  # Silent failure - never block trading due to logging issues

    def _log_rejection(self, asset_name, raw_signal, contract_price, spread_pct, strike_distance_pct,
                       multiplier, time_remaining, recent_move_pct, futures_trend, bid, ask,
                       bid_size, ask_size, spot_price, strike_price, reason):
        """Log every rejected signal with reason to JSONL for later analysis."""
        try:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "asset": asset_name,
                "signal": raw_signal,
                "rejection_reason": reason,
                "contract_price": contract_price,
                "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                "strike_distance_pct": round(strike_distance_pct, 4),
                "multiplier": round(multiplier, 2),
                "time_remaining_sec": int(time_remaining),
                "recent_move_pct": round(recent_move_pct, 6),
                "futures_trend": futures_trend,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "spot_price": spot_price,
                "strike_price": strike_price,
            }
            with open(REJECTION_LOG_PATH, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def _ml_confidence(self, multiplier, strike_distance_pct, recent_move_pct,
                       time_remaining_sec, futures_trend, spread_pct=None):
        """Return predicted probability of YES winning, or None if model unavailable.
        
        Features MUST be in exact training order:
        [multiplier, strike_distance_pct, recent_move_pct, time_remaining_sec, futures_trend, spread_pct]
        """
        if self.ml_model is None:
            return None
        try:
            features = np.array([[multiplier, strike_distance_pct, recent_move_pct,
                                  time_remaining_sec, futures_trend, spread_pct if spread_pct is not None else 0.0]])
            prob = self.ml_model.predict(features)[0]
            return float(prob)
        except Exception:
            return None

    def _estimate_win_prob(self, recent_move_pct, time_remaining_sec, volatility=None):
        """
        Estimate the probability of winning the trade based on:
        - recent_move_pct: how far spot moved in the last 5 min
        - time_remaining_sec: how long until expiry
        - volatility: optional rolling vol for better estimation
        
        Returns: win probability (0.0 to 1.0)
        """
        # Base assumption: mean reversion works better with larger moves and more time
        abs_move = abs(recent_move_pct)
        
        # Larger moves have higher reversion probability (but diminishing returns)
        reversion_factor = min(0.85, 0.50 + abs_move * 150)
        
        # More time remaining = more chance of reversion
        time_factor = min(1.0, time_remaining_sec / 300)  # normalize to 5 min
        
        # Combined probability
        win_prob = 0.50 + (reversion_factor - 0.50) * (0.3 + 0.7 * time_factor)
        
        # Clamp to reasonable range
        return max(0.10, min(0.90, win_prob))

    def _calculate_ev(self, contract_price, win_prob, side):
        """
        Calculate Expected Value of a trade.
        
        For YES bets:
          Payout if win = (1.0 - contract_price) per contract
          Loss if lose = contract_price per contract
          EV = win_prob * (1.0 - contract_price) - (1 - win_prob) * contract_price
        
        For NO bets:
          Payout if win = contract_price per contract (since NO pays 1 when YES is 0)
          Loss if lose = (1.0 - contract_price) per contract
          EV = win_prob * contract_price - (1 - win_prob) * (1.0 - contract_price)
        
        Returns: EV as a fraction of capital risked
        """
        if side == "yes":
            payout = 1.0 - contract_price
            loss_amount = contract_price
        else:
            payout = contract_price
            loss_amount = 1.0 - contract_price
        
        ev = (win_prob * payout) - ((1.0 - win_prob) * loss_amount)
        return ev

    def evaluate_ev(self, asset_name, contract_price, bid, ask, strike, spot_price,
                    multiplier, time_remaining, recent_move_pct, futures_trend=0.0,
                    volatility=None):
        """
        Enhanced evaluation that computes expected value.
        
        Returns: (signal or None, ev, win_prob, details_dict)
        """
        tier = "HIGH_CAP" if asset_name in ASSET_TIERS.get("HIGH_CAP", []) else "ALTCOIN"
        params = TIER_PARAMS.get(tier, {
            "IMPULSE_THRESHOLD_PCT": IMPULSE_THRESHOLD_PCT,
            "STRIKE_PROXIMITY_PCT": STRIKE_PROXIMITY_PCT
        })

        # Determine raw direction from price move
        if recent_move_pct < -params["IMPULSE_THRESHOLD_PCT"]:
            raw_signal = "ENTER_YES"
        elif recent_move_pct > params["IMPULSE_THRESHOLD_PCT"]:
            raw_signal = "ENTER_NO"
        else:
            return (None, 0.0, 0.5, {"reason": "move_below_threshold"})

        # Estimate win probability
        win_prob = self._estimate_win_prob(recent_move_pct, time_remaining, volatility)
        
        # Calculate EV
        ev = self._calculate_ev(contract_price, win_prob, "yes" if raw_signal == "ENTER_YES" else "no")
        
        details = {
            "ev": ev,
            "win_prob": win_prob,
            "raw_signal": raw_signal,
            "contract_price": contract_price,
        }
        
        return (raw_signal, ev, win_prob, details)

    def evaluate(self, asset_name, bid, ask, bid_size=None, ask_size=None, strike=None, spot_price=None,
                 multiplier=1.0, time_remaining=0,
                 recent_move_pct=0.0, futures_trend=0.0, hour_of_day=None,
                 ticker=None, market_meta=None):
        """
        Evaluate market data for entry signals using tiered asset params and spread filters.

        ticker:      full Kalshi market ticker (enables entry filter, distance check, cooldowns)
        market_meta: raw market metadata dict (used to resolve threshold cleanly)

        Returns: (signal, win_prob) tuple.
                 signal is "ENTER_YES", "ENTER_NO", or None.
                 win_prob is float (0-1) or None if no signal.
        """
        # Wrap in try/except so a crash in one asset's ticker never kills the event loop
        try:
            return self._evaluate_inner(asset_name, bid, ask, bid_size, ask_size, strike, spot_price,
                                        multiplier, time_remaining, recent_move_pct, futures_trend,
                                        hour_of_day, ticker=ticker, market_meta=market_meta)
        except Exception as exc:
            print(f"[SignalEngine] evaluate() crashed for {asset_name}: {exc}")
            return (None, None)

    def _evaluate_inner(self, asset_name, bid, ask, bid_size=None, ask_size=None, strike=None, spot_price=None,
                        multiplier=1.0, time_remaining=0,
                        recent_move_pct=0.0, futures_trend=0.0, hour_of_day=None,
                        ticker=None, market_meta=None):
        # -------------------------------------------------------------------
        # 0. NEW: Entry filter (distance, cooldown, reentry, momentum side)
        # -------------------------------------------------------------------
        if ticker and spot_price and spot_price > 0:
            # Update rolling spot history for momentum computation
            now_ts = time.time()
            self._spot_history.append((now_ts, spot_price))
            # Trim to last 10 minutes of data
            cutoff = now_ts - 10 * 60
            self._spot_history = [(t, p) for t, p in self._spot_history if t >= cutoff]

            momentum_bps = compute_momentum_bps(
                self._spot_history,
                lookback_seconds=MOMENTUM_LOOKBACK_MINUTES * 60
            )

            threshold = resolve_contract_threshold(market_meta, ticker)
            if threshold is None:
                try:
                    threshold = float(strike) if strike is not None else None
                except (TypeError, ValueError):
                    threshold = None

            ef_result = self._entry_filter.evaluate(
                ticker=ticker,
                asset=asset_name,
                spot=spot_price,
                threshold=threshold,
                momentum_bps=momentum_bps,
                time_remaining_sec=time_remaining,
                proposed_contracts=MAX_CONTRACTS_PER_TRADE,
                tracker=self.contract_tracker,
                market_meta=market_meta,
            )
            self.contract_tracker.record_signal(ticker)

            window_start, window_end = None, None
            try:
                from engine.entry_filter import parse_window_from_ticker
                window_start, window_end = parse_window_from_ticker(ticker)
            except Exception:
                pass

            log_decision(
                ticker=ticker,
                asset=asset_name,
                spot=spot_price,
                threshold=threshold,
                distance_bps=ef_result["distance_bps"],
                momentum_bps=ef_result["momentum_bps"],
                proposed_side=ef_result.get("approved_side"),
                allowed_size=ef_result.get("approved_contracts", 0),
                result=ef_result,
                window_start=window_start,
                window_end=window_end,
            )

            if ef_result["decision"] == "SKIP":
                self._log_rejection(
                    asset_name, None, None, None, 0.0, multiplier,
                    time_remaining, recent_move_pct, futures_trend,
                    bid, ask, bid_size, ask_size, spot_price, strike,
                    f"entry_filter:{ef_result['skip_reason']}"
                )
                return (None, None)

        # --- Fast early exits (cheapest checks first) ---
        if time_remaining < NO_ENTRY_LAST_SECONDS:
            self._log_rejection(asset_name, None, None, None, 0.0, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                "time_remaining_too_low")
            return (None, None)

        # Multiplier range check (skip when EV mode is active — EV is a superior filter)
        if not USE_EV_ENTRY:
            if multiplier < MIN_MULTIPLIER or multiplier > MAX_MULTIPLIER:
                self._log_rejection(asset_name, None, None, None, 0.0, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"multiplier_out_of_range:{multiplier}")
                return (None, None)

        # 1. Asset Tier Parameter Lookup
        tier = "HIGH_CAP" if asset_name in ASSET_TIERS.get("HIGH_CAP", []) else "ALTCOIN"
        params = TIER_PARAMS.get(tier, {
            "IMPULSE_THRESHOLD_PCT": IMPULSE_THRESHOLD_PCT,
            "STRIKE_PROXIMITY_PCT": STRIKE_PROXIMITY_PCT
        })

        # 2. Spread Filter
        spread_pct = None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            spread_pct = MicroOptimizations.fast_spread_pct(bid, ask)
            if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
                self._log_rejection(asset_name, None, None, spread_pct, 0.0, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"spread_too_wide:{spread_pct:.2%}")
                return (None, None)

        # 3. Strike Proximity Check
        strike_distance_pct = 0.0
        # Normalize strike to float — Kalshi can return it as a string like "63557.76"
        try:
            # First check if strike is None
            if strike is None:
                strike_float = None
            else:
                strike_float = float(strike) if strike is not None else None
        except (TypeError, ValueError):
            strike_float = None
        if strike_float is not None and strike_float > 0:
            try:
                strike_distance_pct = abs(spot_price - strike_float) / strike_float
                if strike_distance_pct > params["STRIKE_PROXIMITY_PCT"]:
                    self._log_rejection(asset_name, None, None, spread_pct, strike_distance_pct, multiplier,
                                        time_remaining, recent_move_pct, futures_trend,
                                        bid, ask, bid_size, ask_size, spot_price, strike,
                                        f"strike_too_far:{strike_distance_pct:.2%}")
                    return (None, None)
            except (TypeError, ZeroDivisionError):
                self._log_rejection(asset_name, None, None, spread_pct, 0.0, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"strike_not_numeric:{strike}")
                return (None, None)
        elif strike is not None:
            # Strike present but not parseable as a positive number
            self._log_rejection(asset_name, None, None, spread_pct, 0.0, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                f"strike_not_numeric:{strike}")
            return (None, None)

        # 4. Determine raw signal
        raw_signal = None
        if recent_move_pct < -params["IMPULSE_THRESHOLD_PCT"]:
            raw_signal = "ENTER_YES"
        elif recent_move_pct > params["IMPULSE_THRESHOLD_PCT"]:
            raw_signal = "ENTER_NO"

        if raw_signal is None:
            self._log_rejection(asset_name, None, None, spread_pct, strike_distance_pct, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                f"move_below_threshold:{recent_move_pct:.6f}")
            return (None, None)
        
        # 5. Price filter: never pay more than $0.50 per contract
        #
        # Two separate values are needed:
        #   actual_cost  — what we pay (YES price for YES bets, NO price for NO bets)
        #   ev_price     — the YES-equivalent price used by the EV formula
        #
        # If the side-specific bid/ask is missing, recover from multiplier:
        #   fast_multiplier(price) = 1.0 / price  →  price = 1.0 / multiplier
        no_price_from_mult = 1.0 / multiplier if multiplier > 0 else None
        if raw_signal == "ENTER_YES":
            actual_cost = ask if ask is not None else no_price_from_mult
            ev_price = actual_cost
        else:
            # For NO bets: actual cost = no_price, EV needs YES-equivalent = 1 - no_price
            no_cost = (1.0 - bid) if bid is not None else no_price_from_mult
            actual_cost = no_cost if no_cost is not None else (1.0 / multiplier if multiplier > 0 else None)
            ev_price = (1.0 - actual_cost) if actual_cost is not None else None
        if actual_cost is None:
            self._log_rejection(asset_name, raw_signal, None, spread_pct, strike_distance_pct, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                f"contract_price_missing:bid={bid}_ask={ask}_mult={multiplier}")
            return (None, None)
        try:
            cost_float = float(actual_cost) if not isinstance(actual_cost, (int, float)) else actual_cost
            if cost_float > 0.50:
                self._log_rejection(asset_name, raw_signal, cost_float, spread_pct, strike_distance_pct, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"contract_price_too_high:{cost_float:.3f}")
                return (None, None)
            
            # Prevent buying extremely cheap options where spread friction is too high
            from config import MIN_CONTRACT_PRICE
            if cost_float < MIN_CONTRACT_PRICE:
                self._log_rejection(asset_name, raw_signal, cost_float, spread_pct, strike_distance_pct, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"contract_price_too_low:{cost_float:.3f}")
                return (None, None)
        except (ValueError, TypeError):
            self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                f"invalid_contract_price:{actual_cost}")
            return (None, None)
        # ev_price is the YES-equivalent used by _calculate_ev
        if ev_price is None or ev_price < 0 or ev_price > 1.0:
            ev_price = actual_cost  # fallback

        # Calculate ML probability as standalone veto (reject low-confidence signals)
        ml_prob = None
        if self.ml_model is not None:
            ml_prob = self._ml_confidence(
                multiplier=multiplier,
                strike_distance_pct=strike_distance_pct,
                recent_move_pct=recent_move_pct,
                time_remaining_sec=time_remaining,
                futures_trend=futures_trend,
                spread_pct=spread_pct
            )

        if USE_ML_VETO and ml_prob is not None and ml_prob < ML_CONFIDENCE_THRESHOLD:
            reason = f"ml_low_confidence:{ml_prob:.4f}"
            self._log_veto(raw_signal, ml_prob, multiplier, strike_distance_pct,
                           recent_move_pct, time_remaining, futures_trend, spread_pct, reason)
            self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                time_remaining, recent_move_pct, futures_trend,
                                bid, ask, bid_size, ask_size, spot_price, strike,
                                reason)
            return (None, None)
        elif ml_prob is not None:
            print(f"[SignalEngine] ML filter passed for {asset_name}: prob={ml_prob:.4f}")
            


        # 6. EV-based profitability prediction (when enabled)
        if USE_EV_ENTRY:
            # Use ML probability as the win probability if available, otherwise fall back to rule-based estimation
            if ml_prob is not None:
                win_prob = ml_prob
                prob_source = "ML"
            else:
                win_prob = self._estimate_win_prob(recent_move_pct, time_remaining)
                prob_source = "rule-based"
                
            ev_side = "yes" if raw_signal == "ENTER_YES" else "no"
            ev = self._calculate_ev(ev_price, win_prob, ev_side)
            
            # Debug: always print EV even if it fails
            print(f"[SignalEngine] EV calc: {raw_signal} | ev={ev:.6f} | win_prob={win_prob:.3f} ({prob_source}) | price={ev_price:.3f} | cost={actual_cost:.3f} | move_pct={recent_move_pct:.6f}")
            
            if ev < MIN_EV_EDGE:
                self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    f"ev_too_low:ev={ev:.4f}_winprob={win_prob:.3f}_{prob_source}")
                return (None, None)
            
            print(f"[SignalEngine] EV check passed: {raw_signal} ev={ev:.4f} win_prob={win_prob:.3f} ({prob_source}) price={ev_price:.3f} cost={actual_cost:.3f}")
            
            # If EV passes, skip remaining rule-based filters (they're already priced in)
            # Note: Exposure limit enforcement is handled by RiskManager.calculate_contracts()
            return (raw_signal, win_prob)

        # --- Legacy rule-based filters (used only when USE_EV_ENTRY=False) ---
        
        # 7. Order Book Imbalance (OBI) Filter
        if bid_size is not None and ask_size is not None:
            total_size = bid_size + ask_size
            if total_size > 0:
                imbalance = bid_size / total_size if raw_signal == "ENTER_YES" else ask_size / total_size
                if imbalance < MIN_BOOK_IMBALANCE:
                    self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                        time_remaining, recent_move_pct, futures_trend,
                                        bid, ask, bid_size, ask_size, spot_price, strike,
                                        f"obi_too_low:{imbalance:.3f}")
                    return (None, None)

        # 8. Time-of-day filter
        if hour_of_day is not None:
            if 2 <= hour_of_day <= 6:
                self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                    time_remaining, recent_move_pct, futures_trend,
                                    bid, ask, bid_size, ask_size, spot_price, strike,
                                    "low_liquidity_hours")
                return (None, None)
        
        # 9. Futures trend alignment
        if futures_trend is not None and futures_trend != 0:
            if raw_signal == "ENTER_YES" and futures_trend < 0:
                if abs(recent_move_pct) < params["IMPULSE_THRESHOLD_PCT"] * 1.5:
                    self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                        time_remaining, recent_move_pct, futures_trend,
                                        bid, ask, bid_size, ask_size, spot_price, strike,
                                        "counter_trend_too_small")
                    return (None, None)
            elif raw_signal == "ENTER_NO" and futures_trend > 0:
                if abs(recent_move_pct) < params["IMPULSE_THRESHOLD_PCT"] * 1.5:
                    self._log_rejection(asset_name, raw_signal, actual_cost, spread_pct, strike_distance_pct, multiplier,
                                        time_remaining, recent_move_pct, futures_trend,
                                        bid, ask, bid_size, ask_size, spot_price, strike,
                                        "counter_trend_too_small")
                    return (None, None)

        # Determine win_prob for non-EV path
        # Priority: ML > rule-based estimation
        if ml_prob is not None:
            win_prob = ml_prob
        else:
            win_prob = self._estimate_win_prob(recent_move_pct, time_remaining)
        
        return (raw_signal, win_prob)