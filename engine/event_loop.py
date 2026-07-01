import asyncio
import calendar
import re
import time
from datetime import datetime

from config import (
    ENABLE_EXECUTION_GUARDS,
    NO_ENTRY_LAST_SECONDS,
    PROFIT_PROTECTION_TRIGGER,
    SANDBOX_MODE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
)
from engine.execution_guard import ExecutionGuard
from engine.inter_window_carry import InterWindowMomentumCarry, IWMCManager
from engine.latency_optimizer import MicroOptimizations
from engine.position_manager import PositionManager
from engine.risk_manager import RiskManager
from engine.signal_engine import SignalEngine
from engine.state_machine import StateMachine


class RegexPatterns:
    PRICE = re.compile(r"\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)")
    TARGET = re.compile(
        r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)(?=\s*target)", re.IGNORECASE
    )
    TARGET_PREFIX = re.compile(
        r"target[^\d]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)", re.IGNORECASE
    )
    BOUNDS = re.compile(r"\b([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)\b")


class EventLoop:
    @staticmethod
    def _snap_price(price: float) -> float:
        """
        Snap a limit order price to Kalshi's tapered_deci_cent grid.

        Price ranges:
          $0.0000 - $0.1000  →  step 0.0010
          $0.1000 - $0.9000  →  step 0.0100
          $0.9000 - $1.0000  →  step 0.0010

        This prevents 'invalid_price' rejections from Kalshi's API.
        """
        if price < 0.10:
            return round(max(0.001, price) * 1000) / 1000
        elif price < 0.90:
            return round(max(0.01, price) * 100) / 100
        else:
            return round(min(0.999, price) * 1000) / 1000

    def __init__(
        self,
        kalshi_client,
        futures_client,
        coinbase_client=None,
        asset="BTC",
        trade_logger=None,
        global_exposures=None,
        global_reservations=None,
        portfolio_positions=None,
    ):
        self.asset = str(asset).upper()
        self.kalshi = kalshi_client
        self.futures = futures_client
        self.coinbase = coinbase_client
        self.signal = SignalEngine()
        self.position = PositionManager()
        if hasattr(self, "futures"):
            self.futures.on_tick_callback = self._on_futures_tick
        self.l2_bids = {}
        self.l2_asks = {}
        from engine.exit_engine import ExitEngine

        self.exit_engine = ExitEngine()
        self.state = StateMachine()
        self.risk = RiskManager(
            global_exposure_dict=global_exposures,
            global_reservations_dict=global_reservations,
        )
        self.execution_guard = ExecutionGuard()
        self.trade_logger = trade_logger
        self.asset_exposure = {}  # Track exposure per asset
        self._portfolio_positions_ref = portfolio_positions

        self.current_ticker = None
        self.strike = None
        self.expiry = None
        self._debugged_first_ticker = False
        self._last_position_sync = 0
        self._ticker_in_flight = False
        self._order_in_flight = False
        self._market_rollover_in_flight = False
        self._entry_timestamp = 0
        self._entry_time_iso = ""
        self._current_multiplier = 1.0  # Track current multiplier
        self._stop_loss_cooldown_until = (
            0.0  # Timestamp until which new entries are blocked
        )
        self._post_trade_cooldown_until = (
            0.0  # Timestamp until which new entries are blocked after ANY trade
        )
        self._window_trade_direction: dict[
            str, str
        ] = {}  # ticker -> "yes" or "no" (first trade direction per window)
        self._last_entry_fee = (
            0.0  # Entry fee from last trade (for CSV logging at exit)
        )

        # Inter-Window Momentum Carry (IWMC) Strategy
        self.iwmc = InterWindowMomentumCarry(self.asset)
        # Shared cross-asset IWMC manager (attached by main.py via initialize_asset_loop).
        # Signal evaluation routes through iwmc_manager, NOT self.iwmc, because the
        # manager's instances share the settlement history recorded on window expiry.
        self.iwmc_manager = None  # type: IWMCManager  # set dynamically by main.py
        # Contract YES price captured at the start of the current window.
        # Used as window_open_price for IWMC settlement momentum (NOT self.strike,
        # which is the underlying settlement threshold, not the contract price).
        self._window_open_yes_price = None

        # CRMD: CFB RTI Momentum Divergence Strategy
        # Fires in final 180s when RTI momentum diverges from Kalshi contract price.
        # Independent of IWMC_ONLY_MODE — always evaluated.
        from engine.rti_momentum_drift import RTIMomentumDrift

        self.crmd = RTIMomentumDrift(self.asset)

        # SKEW_FADE: Orderbook Skew Fade Strategy
        # Fades extreme orderbook imbalances in mid-window. Independent of IWMC_ONLY_MODE.
        from config import (
            ENABLE_SKEW_FADE,
            SKEW_MIN_TOTAL_DEPTH,
            SKEW_SIGNAL_COOLDOWN,
            SKEW_THRESHOLD,
        )
        from engine.orderbook_skew_fade import OrderbookSkewFade

        self.skew_fade = OrderbookSkewFade(
            self.asset,
            skew_threshold=SKEW_THRESHOLD,
            min_total_depth=SKEW_MIN_TOTAL_DEPTH,
            signal_cooldown=SKEW_SIGNAL_COOLDOWN,
        )

        # Pre-market discovery cache: discovered during last 120s of current window
        # so rollover is instant (no blocking REST calls).
        self._cached_next_market = None  # (ticker, strike, expiry, open_yes) or None
        self._pre_discovery_done = False

        self.series_candidates = [f"KX{self.asset}15M"]

        self._portfolio_balance_ref = None

        self.current_ticker = None
        self.strike = None
        self.expiry = None
        self._debugged_first_ticker = False
        self._last_position_sync = 0
        self._ticker_in_flight = False
        self._order_in_flight = False
        self._market_rollover_in_flight = False
        self._entry_timestamp = 0
        self._entry_time_iso = ""
        self._current_multiplier = 1.0  # Track current multiplier
        self._stop_loss_cooldown_until = (
            0.0  # Timestamp until which new entries are blocked
        )
        self._post_trade_cooldown_until = (
            0.0  # Timestamp until which new entries are blocked after ANY trade
        )
        self._window_trade_direction: dict[
            str, str
        ] = {}  # ticker -> "yes" or "no" (first trade direction per window)
        self._last_entry_fee = (
            0.0  # Entry fee from last trade (for CSV logging at exit)
        )

        self._yes_price_history: list[tuple[float, float]] = []  # (unix_ts, yes_price)
        self._yes_price_velocity: float = (
            0.0  # bps/sec, positive=rising, negative=falling
        )

        # Spot price history for IWMC volatility estimation (last 60 1-min prices)
        self._spot_price_history: list[
            tuple[float, float]
        ] = []  # (unix_ts, spot_price)

    async def _maybe_place_market(self, **kw):
        """Place market order with fill validation. Retry with wider price if fill is 0."""
        from config import IOC_MIN_FILL_RETRY, IOC_RETRY_PRICE_BUFFER

        max_retries = IOC_MIN_FILL_RETRY
        price_buffer = IOC_RETRY_PRICE_BUFFER

        for attempt in range(max_retries + 1):
            order_result = await self.kalshi.place_market_order(**kw)
            order_details = (
                order_result.get("order") or order_result
                if isinstance(order_result, dict)
                else {}
            )
            raw_fill = (
                order_details.get("fill_count")
                or order_details.get("fill_count_fp")
                or 0
            )
            filled = int(float(raw_fill)) if raw_fill else 0

            if filled > 0:
                return order_result

            if attempt < max_retries:
                # Retry with wider price buffer
                if "price" in kw and kw["price"] is not None:
                    action = kw.get("action", "buy")
                    if action == "buy":
                        kw["price"] = min(0.99, kw["price"] + price_buffer)
                    else:
                        kw["price"] = max(0.01, kw["price"] - price_buffer)
                    print(
                        f"[{self.asset}] IoC fill was 0, retrying with price buffer (attempt {attempt + 1}/{max_retries})"
                    )
                else:
                    print(
                        f"[{self.asset}] IoC fill was 0, retrying (attempt {attempt + 1}/{max_retries})"
                    )
            else:
                print(
                    f"[{self.asset}] IoC fill validation failed after {max_retries + 1} attempts, returning 0-fill result"
                )

        return order_result

    async def _maybe_place_limit(self, **kw):
        return await self.kalshi.place_limit_order(**kw)

    async def _maybe_cancel(self, order_id):
        return await self.kalshi.cancel_order(order_id)

    async def start_futures(self):
        await self.futures.connect()

    async def start_coinbase(self):
        if self.coinbase:
            self.coinbase.on_tick_callback = self._on_coinbase_tick
            await self.coinbase.connect()

    async def initialize(self):
        await self.discover_market()
        await self.load_existing_position()
        await self.start_coinbase()

    async def _on_coinbase_tick(self, price: float):
        # Coinbase feeds the macro regime detector for trend/vol analysis
        # Panic fade is now fed by Kalshi contract prices (see _handle_ticker_inner)
        self.risk.macro_regime.update(price)

    async def load_existing_position(self):
        """Load existing position from Kalshi portfolio."""
        try:
            # Prefer reference cache to avoid network hits, fallback to API if not initialized
            if (
                self._portfolio_positions_ref is not None
                and self._portfolio_positions_ref.get("value") is not None
            ):
                positions = self._portfolio_positions_ref["value"]
            else:
                positions = await self.kalshi.get_open_positions()
            if positions and self.current_ticker:
                for pos in positions:
                    pos_ticker = pos.get("market_ticker") or pos.get("ticker")
                    if pos_ticker == self.current_ticker:
                        raw_contracts = (
                            pos.get("position")
                            or pos.get("count")
                            or pos.get("contracts")
                            or 0
                        )
                        self.position.contracts = abs(int(float(raw_contracts)))

                        entry_price = (
                            pos.get("entry_price")
                            or pos.get("average_fill_price")
                            or pos.get("avg_entry_price")
                            or pos.get("price")
                            or 0
                        )
                        try:
                            entry_price = float(entry_price)
                            if entry_price > 1:
                                entry_price = entry_price / 100
                        except Exception:
                            entry_price = 0

                        self.position.entry_price = entry_price
                        raw_side = pos.get("side") or pos.get("outcome_side") or ""
                        self.position.side = raw_side
                        self.position.position_type = (
                            "buy" if raw_side == "yes" else "sell"
                        )

                        if self.position.entry_price and self.position.contracts:
                            position_value = (
                                self.position.entry_price * self.position.contracts
                            )
                            self.risk.set_asset_exposure(self.asset, position_value)

                        self._entry_timestamp = time.time()
                        self._entry_time_iso = datetime.utcnow().isoformat()

                        print(
                            f"[{self.asset}] Loaded existing position: {self.position.contracts} contracts at ${self.position.entry_price:.2f}, exposure: ${self.risk.get_asset_exposure(self.asset):.2f}"
                        )
                        break
        except Exception as e:
            print(f"[{self.asset}] Failed to load existing position: {e}")

    def market_expired(self):
        return self.expiry is not None and int(time.time()) >= self.expiry

    async def rollover_market(self):
        if self._market_rollover_in_flight:
            return False

        self._market_rollover_in_flight = True
        old_ticker = self.current_ticker
        try:
            print(f"[{self.asset}] Market expired; rolling to next 15-minute market.")
            # Clear exposure when market expires
            old_exposure = self.risk.get_asset_exposure(self.asset)
            if old_exposure > 0:
                print(
                    f"[{self.asset}] Clearing exposure of ${old_exposure:.2f} for expired market"
                )
                self.risk.set_asset_exposure(self.asset, 0.0)
            self.state.exit()
            self.position.close()
            self._debugged_first_ticker = False
            self._last_position_sync = 0
            if old_ticker and old_ticker in self._window_trade_direction:
                del self._window_trade_direction[old_ticker]

            # Use pre-discovered market cache (instant) or fall back to blocking discover
            if self._cached_next_market is not None:
                next_ticker, next_strike, next_expiry, next_open_yes = (
                    self._cached_next_market
                )
                self.current_ticker = next_ticker
                self.strike = next_strike
                self.expiry = next_expiry
                self._window_open_yes_price = next_open_yes
                self.latest_ticker_data = None

                # Reset for next round of pre-discovery
                self._cached_next_market = None
                self._pre_discovery_done = False

                print(
                    f"[{self.asset}] ROLLOVER (cached): {old_ticker} -> {next_ticker} "
                    f"strike={next_strike} open_yes=${next_open_yes:.3f}"
                )

                # Subscribe to the new ticker via shared WebSocket
                if next_ticker:
                    self.kalshi.subscribe_ticker(next_ticker)
            else:
                # Fallback: blocking discover (slow — can miss IWMC window)
                print(
                    f"[{self.asset}] WARNING: No cached market, falling back to slow discover_market()"
                )
                await self.discover_market()

            await self.load_existing_position()
            changed = self.current_ticker != old_ticker
            print(
                f"[{self.asset}] Market rollover:",
                old_ticker,
                "->",
                self.current_ticker,
            )
            return changed
        except Exception as exc:
            print(f"[{self.asset}] Market rollover failed:", exc)
            return False
        finally:
            self._market_rollover_in_flight = False

    async def sync_current_position(self):
        now = time.time()
        if (
            now - self._last_position_sync < 30
        ):  # Increased from 5s to 30s to reduce API load
            return
        self._last_position_sync = now

        # Always pull fresh data from REST API to prevent stale websocket caching bugs
        # Retry with exponential backoff on failure
        max_retries = 5  # Increased from 3 to 5
        base_delay = 2.0  # Increased from 1.0 to 2.0
        open_positions = None

        for attempt in range(max_retries):
            try:
                open_positions = await self.kalshi.get_open_positions()
                break  # Success
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    print(
                        f"[{self.asset}] get_open_positions failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    print(
                        f"[{self.asset}] get_open_positions failed after {max_retries} attempts: {e}"
                    )
                    return  # skip sync this cycle

        if open_positions is None:
            return

        net_contracts = 0
        total_yes_cost = 0.0
        total_no_cost = 0.0

        for position in open_positions:
            ticker = (
                position.get("market_ticker")
                or position.get("ticker")
                or position.get("instrument_ticker")
                or position.get("event_ticker")
            )
            if ticker != self.current_ticker:
                continue

            status = position.get("status")
            if status is not None:
                status = str(status).lower()
                if status and status not in ("open", "active", "unsettled"):
                    continue

            side = (
                position.get("side")
                or position.get("outcome_side")
                or position.get("bet_side")
            )
            if side not in ("yes", "no"):
                position_fp = position.get("position_fp") or position.get("count_fp")
                if position_fp is not None:
                    try:
                        side = "yes" if float(position_fp) > 0 else "no"
                    except Exception:
                        side = None

            if side not in ("yes", "no"):
                continue

            raw_contracts = (
                position.get("position")
                or position.get("count")
                or position.get("contracts")
                or position.get("quantity")
                or position.get("position_fp")
                or position.get("remaining_count_fp")
                or position.get("count_fp")
            )
            try:
                contracts = abs(int(float(raw_contracts or 0)))
            except Exception:
                contracts = 0

            if contracts <= 0:
                continue

            entry_price = (
                position.get("entry_price")
                or position.get("average_fill_price")
                or position.get("fill_price")
                or position.get("price")
            )
            if entry_price is not None:
                try:
                    entry_price = float(entry_price)
                    if entry_price > 1:
                        entry_price = entry_price / 100
                except Exception:
                    entry_price = 0.0
            else:
                entry_price = 0.0

            if side == "yes":
                net_contracts += contracts
                total_yes_cost += entry_price * contracts
            elif side == "no":
                net_contracts -= contracts
                total_no_cost += entry_price * contracts

        if net_contracts == 0:
            if not self.state.can_enter():
                print(
                    f"[{self.asset}] Net portfolio position is flat (0 contracts); resetting state."
                )
                if self.risk.get_asset_exposure(self.asset) > 0:
                    self.risk.set_asset_exposure(self.asset, 0.0)
                self.state.exit()
                self.position.close()
            return

        final_side = "yes" if net_contracts > 0 else "no"
        final_contracts = abs(net_contracts)

        if final_side == "yes":
            avg_entry = (
                (total_yes_cost - total_no_cost) / final_contracts
                if final_contracts > 0
                else 0.0
            )
        else:
            avg_entry = (
                (total_no_cost - total_yes_cost) / final_contracts
                if final_contracts > 0
                else 0.0
            )

        # Preserve local entry_price when API returns obviously bogus values (<$0.05)
        # This prevents phantom PnL spikes when Kalshi returns avg_entry_price=0
        local_entry = (
            self.position.entry_price if self.position.entry_price is not None else 0.0
        )
        if avg_entry < 0.05 and local_entry >= 0.08:
            avg_entry = local_entry
            print(
                f"[{self.asset}] Preserved local entry_price=${local_entry:.2f} over API "
                f"value=${avg_entry:.4f} (API returned bogus entry_price)"
            )
        else:
            avg_entry = max(0.01, avg_entry)

        signal = "ENTER_YES" if final_side == "yes" else "ENTER_NO"
        self.state.enter(signal)
        self.position.open(
            avg_entry,
            final_contracts,
            final_side,
            position_type="buy" if final_side == "yes" else "sell",
            reset_peak=False,
        )

        if avg_entry and final_contracts:
            self.risk.set_asset_exposure(self.asset, avg_entry * final_contracts)

        # Only set entry timestamp on first-time sync (don't overwrite on periodic refresh)
        if self._entry_timestamp == 0:
            self._entry_timestamp = time.time()
            self._entry_time_iso = datetime.utcnow().isoformat()

        print(
            f"[{self.asset}] Synced NET portfolio position: {self.current_ticker} {final_side} {final_contracts} avg_entry=${avg_entry:.2f} exposure=${self.risk.get_asset_exposure(self.asset):.2f}"
        )

    async def _execute_exit(
        self,
        contracts: int,
        close_action: str,
        aggressive_price: float,
        fallback_value: float,
    ):
        """
        Exit a position using aggressive LIMIT orders with retry logic.
        Max 3 LIMIT retries, then force IoC MARKET exit.
        Returns (filled_count, total_fee_usd, average_fill_price).
        """
        from config import EXIT_RETRY_COOLDOWN_SECONDS, MAX_EXIT_LIMIT_RETRIES

        if contracts <= 0:
            return 0, 0.0, fallback_value

        total_filled = 0
        total_fee = 0.0
        weighted_price_sum = 0.0
        remaining = contracts

        for attempt in range(
            MAX_EXIT_LIMIT_RETRIES + 1
        ):  # +1 for the final MARKET attempt
            is_final_attempt = attempt == MAX_EXIT_LIMIT_RETRIES
            client_oid = f"exit_{self.asset}_{int(time.time() * 1000)}_a{attempt}"

            try:
                if is_final_attempt:
                    # Final attempt: force IoC MARKET
                    print(
                        f"[{self.asset}] Exit attempt {attempt + 1}/{MAX_EXIT_LIMIT_RETRIES + 1}: FORCING IoC MARKET for {remaining} contracts"
                    )
                    order_result = await self._maybe_place_market(
                        ticker=self.current_ticker,
                        side="yes",
                        contracts=remaining,
                        action=close_action,
                        reduce_only=True,
                    )
                else:
                    # LIMIT order attempts
                    # Snap to Kalshi's valid price grid to avoid 'invalid_price' rejections
                    snapped_price = self._snap_price(aggressive_price)
                    print(
                        f"[{self.asset}] Exit attempt {attempt + 1}/{MAX_EXIT_LIMIT_RETRIES + 1}: LIMIT at ${snapped_price:.2f} for {remaining} contracts"
                    )
                    order_result = await self._maybe_place_limit(
                        ticker=self.current_ticker,
                        side="yes",
                        contracts=remaining,
                        price=snapped_price,
                        action=close_action,
                        client_order_id=client_oid,
                    )

                order_details = (
                    order_result.get("order") or order_result
                    if isinstance(order_result, dict)
                    else {}
                )
                raw_fill = (
                    order_details.get("fill_count")
                    or order_details.get("fill_count_fp")
                    or 0
                )
                filled = int(float(raw_fill)) if raw_fill else 0
                fee = float(order_details.get("average_fee_paid") or 0) * filled
                avg_fill = float(
                    order_details.get("average_fill_price") or aggressive_price
                )

                if filled > 0:
                    total_filled += filled
                    total_fee += fee
                    weighted_price_sum += filled * avg_fill
                    remaining -= filled
                    print(
                        f"[{self.asset}] Exit filled: {filled} contracts at ${avg_fill:.2f} (fee=${fee:.3f}), {remaining} remaining"
                    )

                if remaining <= 0:
                    # Fully filled
                    blended_price = (
                        weighted_price_sum / total_filled
                        if total_filled > 0
                        else fallback_value
                    )
                    print(
                        f"[{self.asset}] Exit COMPLETE: {total_filled} contracts at blended ${blended_price:.2f} (total fee=${total_fee:.3f})"
                    )
                    return total_filled, total_fee, blended_price

                # Partial fill - wait before retry (except on final attempt)
                if not is_final_attempt and remaining > 0:
                    print(
                        f"[{self.asset}] Partial fill ({filled}/{contracts}). Waiting {EXIT_RETRY_COOLDOWN_SECONDS}s before retry..."
                    )
                    await asyncio.sleep(EXIT_RETRY_COOLDOWN_SECONDS)

            except Exception as e:
                print(f"[{self.asset}] Exit attempt {attempt + 1} failed: {e}")
                if is_final_attempt:
                    # On final attempt failure, return what we have
                    blended_price = (
                        weighted_price_sum / total_filled
                        if total_filled > 0
                        else fallback_value
                    )
                    return total_filled, total_fee, blended_price
                # Wait before retry on error
                await asyncio.sleep(EXIT_RETRY_COOLDOWN_SECONDS)

        # All attempts exhausted
        blended_price = (
            weighted_price_sum / total_filled if total_filled > 0 else fallback_value
        )
        print(
            f"[{self.asset}] Exit EXHAUSTED: {total_filled}/{contracts} filled at blended ${blended_price:.2f} (fee=${total_fee:.3f})"
        )
        return total_filled, total_fee, blended_price

    async def _pre_discover_market(self):
        """
        Discover the next 15-minute market in the background during the last 120s
        of the current window. Caches the result so rollover is instant.
        Returns (ticker, strike, expiry, open_yes) or None.
        """
        if self._pre_discovery_done:
            return self._cached_next_market

        try:
            spot = self.futures.get_spot()
            now = int(time.time())

            # Find the next window's close time (next 15-min boundary after current expiry)
            if self.expiry:
                # Target the next window that starts right after this one ends
                # The next window opens at self.expiry and closes 15 min later
                min_close_ts = self.expiry
                max_close_ts = self.expiry + 20 * 60
            else:
                min_close_ts = now + NO_ENTRY_LAST_SECONDS
                max_close_ts = now + 20 * 60

            def parse_event_time(value):
                if not value:
                    return None
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                try:
                    parsed = time.strptime(
                        value.replace("+00:00", "Z"), "%Y-%m-%dT%H:%M:%SZ"
                    )
                    return calendar.timegm(parsed)
                except Exception:
                    return None

            def parse_ticker_fallback(ticker):
                try:
                    date_part = ticker.split("-", 1)[1]
                    day = int(date_part[:2])
                    month_str = date_part[2:5]
                    hour = int(date_part[5:7])
                    minute = int(date_part[7:9])
                    month_map = {
                        "JAN": 1,
                        "FEB": 2,
                        "MAR": 3,
                        "APR": 4,
                        "MAY": 5,
                        "JUN": 6,
                        "JUL": 7,
                        "AUG": 8,
                        "SEP": 9,
                        "OCT": 10,
                        "NOV": 11,
                        "DEC": 12,
                    }
                    month = month_map.get(month_str)
                    if month is None:
                        return None
                    expiry_struct = time.struct_time(
                        (time.gmtime().tm_year, month, day, hour, minute, 0, 0, 0, 0)
                    )
                    return calendar.timegm(expiry_struct)
                except Exception:
                    return None

            def parse_event_target(event):
                text_fields = [
                    event.get("title"),
                    event.get("sub_title"),
                    event.get("subtitle"),
                    event.get("name"),
                    event.get("description"),
                    event.get("event_description"),
                ]
                for text in text_fields:
                    if not text:
                        continue
                    text = str(text)
                    match = RegexPatterns.PRICE.search(text)
                    if match:
                        try:
                            return float(match.group(1).replace(",", ""))
                        except Exception:
                            return None
                    match = RegexPatterns.TARGET.search(text)
                    if match:
                        try:
                            return float(match.group(1).replace(",", ""))
                        except Exception:
                            return None
                    match = RegexPatterns.TARGET_PREFIX.search(text)
                    if match:
                        try:
                            return float(match.group(1).replace(",", ""))
                        except Exception:
                            return None
                return None

            def parse_market_strike(market):
                strike = (
                    market.get("strike_price")
                    or market.get("strike")
                    or market.get("floor_strike")
                )
                if strike is not None:
                    try:
                        return float(strike)
                    except TypeError, ValueError:
                        return None
                return None

            def is_yes_market(market):
                title = str(market.get("title") or "").lower()
                if "up" in title and "down" not in title:
                    return True
                if "above" in title and "below" not in title:
                    return True
                return False

            def is_yes_no_market(market):
                title = str(market.get("title") or "").lower()
                if "up" in title and "down" in title:
                    return True
                if "above" in title and "below" in title:
                    return True
                return False

            selected_market = None
            preferred_yes_market = None

            try:
                url = (
                    f"/events?limit=200&series_ticker={'&series_ticker='.join(self.series_candidates)}"
                    f"&with_nested_markets=true&min_close_ts={min_close_ts}&max_close_ts={max_close_ts}"
                )
                events_data = await self.kalshi.authenticated_request("GET", url)
                events = (
                    events_data.get("events")
                    if isinstance(events_data, dict)
                    else events_data
                ) or []

                for event in events:
                    event_close = parse_event_time(
                        event.get("close_time") or event.get("expected_close_time")
                    )
                    if event_close is None:
                        continue
                    event_strike = parse_event_target(event)

                    markets = event.get("markets") or []
                    for market in markets:
                        ticker = market.get("ticker")
                        market_strike = parse_market_strike(market)
                        market_close = parse_event_time(
                            market.get("close_time")
                            or market.get("expected_close_time")
                        )
                        if market_close:
                            market_strike = market_strike or event_strike
                            if (
                                market_close >= min_close_ts
                                and market_close <= max_close_ts
                            ):
                                if is_yes_no_market(market):
                                    if not preferred_yes_market:
                                        preferred_yes_market = (
                                            ticker,
                                            market_strike,
                                            market_close,
                                        )
                                elif is_yes_market(market):
                                    if not preferred_yes_market:
                                        preferred_yes_market = (
                                            ticker,
                                            market_strike,
                                            market_close,
                                        )

                selected_market = preferred_yes_market or selected_market
            except Exception as e:
                print(f"[{self.asset}] Pre-discovery fetch failed: {e}")

            if not selected_market:
                print(f"[{self.asset}] Pre-discovery: no next window found")
                return None

            next_ticker, next_strike, next_expiry = selected_market

            # Fetch the next market's initial price
            open_yes = None
            try:
                market_data = await self.kalshi.authenticated_request(
                    "GET", f"/markets/{next_ticker}"
                )
                market = market_data.get("market") or market_data or {}
                open_yes = (
                    MicroOptimizations.fast_normalize_price(
                        market.get("yes_ask_dollars")
                    )
                    or MicroOptimizations.fast_normalize_price(market.get("yes_ask"))
                    or MicroOptimizations.fast_normalize_price(
                        market.get("yes_bid_dollars")
                    )
                    or MicroOptimizations.fast_normalize_price(market.get("yes_bid"))
                    or MicroOptimizations.fast_normalize_price(
                        market.get("yes_price_dollars")
                    )
                    or MicroOptimizations.fast_normalize_price(market.get("yes_price"))
                    or MicroOptimizations.fast_normalize_price(
                        market.get("last_price_dollars")
                    )
                    or MicroOptimizations.fast_normalize_price(market.get("last_price"))
                )
            except Exception as e:
                print(f"[{self.asset}] Pre-discovery price fetch failed: {e}")

            self._cached_next_market = (next_ticker, next_strike, next_expiry, open_yes)
            self._pre_discovery_done = True
            print(
                f"[{self.asset}] Pre-discovery READY: {next_ticker} strike={next_strike} "
                f"open_yes=${open_yes:.3f}"
            )
            return self._cached_next_market

        except Exception as e:
            # Always mark done so we don't retry forever
            self._pre_discovery_done = True
            print(f"[{self.asset}] Pre-discovery failed: {e}")
            return None

    async def discover_market(self):
        print(f"Discovering {self.asset} market...")

        spot = self.futures.get_spot()
        print(f"{self.asset} spot price:", spot)

        now = int(time.time())
        window_seconds = 20 * 60
        min_entry_ts = now + NO_ENTRY_LAST_SECONDS

        def parse_event_time(value):
            if not value:
                return None

            if value.endswith("Z"):
                value = value[:-1] + "+00:00"

            try:
                parsed = time.strptime(
                    value.replace("+00:00", "Z"), "%Y-%m-%dT%H:%M:%SZ"
                )
                return calendar.timegm(parsed)
            except Exception:
                return None

        def parse_ticker_fallback(ticker):
            try:
                date_part = ticker.split("-", 1)[1]
                day = int(date_part[:2])
                month_str = date_part[2:5]
                hour = int(date_part[5:7])
                minute = int(date_part[7:9])

                month_map = {
                    "JAN": 1,
                    "FEB": 2,
                    "MAR": 3,
                    "APR": 4,
                    "MAY": 5,
                    "JUN": 6,
                    "JUL": 7,
                    "AUG": 8,
                    "SEP": 9,
                    "OCT": 10,
                    "NOV": 11,
                    "DEC": 12,
                }
                month = month_map.get(month_str)
                if month is None:
                    return None

                expiry_struct = time.struct_time(
                    (time.gmtime().tm_year, month, day, hour, minute, 0, 0, 0, 0)
                )
                return calendar.timegm(expiry_struct)
            except Exception:
                return None

        def parse_event_target(event):
            text_fields = [
                event.get("title"),
                event.get("sub_title"),
                event.get("subtitle"),
                event.get("name"),
                event.get("description"),
                event.get("event_description"),
            ]

            for text in text_fields:
                if not text:
                    continue
                text = str(text)

                match = RegexPatterns.PRICE.search(text)
                if match:
                    try:
                        return float(match.group(1).replace(",", ""))
                    except Exception:
                        return None

                match = RegexPatterns.TARGET.search(text)
                if match:
                    try:
                        return float(match.group(1).replace(",", ""))
                    except Exception:
                        return None

                match = RegexPatterns.TARGET_PREFIX.search(text)
                if match:
                    try:
                        return float(match.group(1).replace(",", ""))
                    except Exception:
                        return None

            for text in text_fields:
                if not text:
                    continue
                for match in RegexPatterns.BOUNDS.finditer(str(text)):
                    try:
                        value = float(match.group(1).replace(",", ""))
                        if value >= 1000:
                            return value
                    except Exception:
                        continue

            return None

        def parse_market_strike(ticker):
            if not ticker:
                return None

            if "-B" in ticker:
                try:
                    return float(ticker.split("-B")[1])
                except Exception:
                    pass

            parts = ticker.split("-")
            for part in reversed(parts):
                if not part:
                    continue
                try:
                    strike = float(part)
                    if spot is not None and spot > 0:
                        if abs(strike - spot) / spot <= 0.15:
                            return strike
                    else:
                        if strike >= 1000:
                            return strike
                except Exception:
                    if part.upper().startswith("B"):
                        try:
                            val = float(part[1:])
                            if spot is not None and spot > 0:
                                if abs(val - spot) / spot <= 0.15:
                                    return val
                            else:
                                return val
                        except Exception:
                            pass
            return None

        def is_yes_market(ticker):
            return str(ticker).upper().endswith("-YES")

        def is_yes_no_market(ticker):
            return str(ticker).upper().endswith(("-YES", "-NO"))

        selected_event = None
        selected_event_markets = None
        selected_event_expiry = float("inf")
        selected_series = None

        for series in self.series_candidates:
            for search_phase in ("short", "future"):
                cursor = None
                page = 0
                while True:
                    page += 1
                    path = (
                        f"/events?limit=200&series_ticker={series}"
                        "&with_nested_markets=true"
                    )
                    if search_phase == "short":
                        path += (
                            f"&min_close_ts={now}&max_close_ts={now + window_seconds}"
                        )
                    else:
                        path += f"&min_close_ts={now}"

                    if cursor:
                        path += f"&cursor={cursor}"

                    print(f"Querying Kalshi: {path}")
                    try:
                        data = await asyncio.wait_for(
                            self.kalshi.authenticated_request("GET", path), timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        print(
                            f"Market discovery timed out for {series}, moving to next..."
                        )
                        break
                    except asyncio.CancelledError:
                        print(
                            f"Market discovery cancelled for {series} (timeout or shutdown), moving to next..."
                        )
                        break
                    except Exception as e:
                        print(f"Error during market discovery for {series}: {e}")
                        break
                    events = data.get("events", [])

                    print(f"Kalshi events page {page}: {len(events)} events")

                    for event in events:
                        event_ticker = event.get("event_ticker", "")
                        if not event_ticker.startswith(series + "-"):
                            continue

                        # Prioritize actual expiration over trading close time so we don't dump 5 mins early
                        expiry = parse_event_time(event.get("expiration_time"))
                        if expiry is None:
                            expiry = parse_event_time(event.get("strike_date"))
                        if expiry is None:
                            expiry = parse_event_time(event.get("close_time"))
                        if expiry is None:
                            expiry = parse_event_time(event.get("strike_time"))
                        if expiry is None:
                            expiry = parse_ticker_fallback(event_ticker)

                        if expiry is None or expiry <= now:
                            continue

                        if expiry <= min_entry_ts:
                            continue

                        if expiry < selected_event_expiry:
                            selected_event_expiry = expiry
                            selected_event = event
                            selected_event_markets = event.get("markets", [])
                            selected_series = series

                    if selected_event and search_phase == "short":
                        break

                    cursor = data.get("cursor")
                    if not cursor:
                        break

                if selected_event:
                    break
            if selected_event:
                break

        if not selected_event:
            raise Exception(f"No active {self.asset} event found.")

        soonest_event = selected_event.get("event_ticker")
        event_status = selected_event.get("status")
        event_title = selected_event.get("title") or selected_event.get("sub_title")
        print(
            "Selected event:",
            soonest_event,
            "series=",
            selected_series,
            "status=",
            event_status,
            "title=",
            event_title,
        )
        print(
            "Event expiry:",
            selected_event_expiry,
            "seconds from now:",
            selected_event_expiry - now,
        )

        if selected_event_expiry - now <= window_seconds:
            print(f"Selected current 15-minute {self.asset} event:", soonest_event)
        else:
            print(
                f"No 15-minute {self.asset} event found; rejecting {soonest_event} to prevent trading hour-long contracts."
            )
            raise Exception("No valid 15-minute event within window.")

        markets = selected_event_markets or []
        if not markets:
            try:
                markets_data = await asyncio.wait_for(
                    self.kalshi.authenticated_request(
                        "GET", f"/markets?event_ticker={soonest_event}&limit=200"
                    ),
                    timeout=30.0,
                )
                markets = markets_data.get("markets", [])
            except asyncio.TimeoutError:
                print(f"Markets lookup timed out for {soonest_event}")
                raise Exception(f"Could not fetch markets for {soonest_event}")

        event_strike = parse_event_target(selected_event)
        print("Parsed event target strike:", event_strike)
        if event_strike is None:
            print(
                "Warning: could not parse event target from event text, falling back to market strike parsing."
            )

        selected_market = None
        preferred_yes_market = None
        fallback_market = None
        smallest_distance = float("inf")

        for market in markets:
            ticker = market.get("ticker", "")
            close_time = market.get("close_time")

            if not ticker or not close_time:
                continue

            expiry = parse_event_time(close_time)
            if expiry is None or expiry <= now:
                continue

            if event_strike is not None:
                strike = event_strike
            else:
                # Try to parse from market titles
                strike = None
                text_fields = [
                    market.get("title"),
                    market.get("subtitle"),
                    market.get("yes_sub_title"),
                    market.get("no_sub_title"),
                ]
                for text in text_fields:
                    if text:
                        match = RegexPatterns.PRICE.search(str(text))
                        if match:
                            try:
                                strike = float(match.group(1).replace(",", ""))
                                break
                            except Exception:
                                pass
                if strike is None:
                    strike = parse_market_strike(ticker)

            if (
                strike is None
                and event_strike is None
                and market.get("market_type") != "binary"
            ):
                continue

            if is_yes_market(ticker):
                preferred_yes_market = (ticker, strike, expiry)

            fallback_market = (ticker, strike, expiry)
            distance = abs(event_strike - strike) if event_strike is not None else 0
            if distance < smallest_distance:
                smallest_distance = distance
                selected_market = (ticker, strike, expiry)

        selected_market = preferred_yes_market or selected_market or fallback_market

        if not selected_market:
            print("Available markets:")
            for market in markets:
                print(
                    "  market",
                    market.get("ticker"),
                    "close_time",
                    market.get("close_time"),
                )
            raise Exception("No strike markets found for selected event.")

        self.current_ticker, self.strike, self.expiry = selected_market

        print(f"Selected {self.asset} market:")
        print("Ticker:", self.current_ticker)
        print("Strike:", self.strike)

        # Fetch initial market prices to jumpstart the bot immediately
        try:
            market_data = await self.kalshi.authenticated_request(
                "GET", f"/markets/{self.current_ticker}"
            )
            market = market_data.get("market") or market_data or {}
            self.latest_ticker_data = {"type": "ticker", "msg": market}
            # Capture the contract YES price at window open for IWMC settlement momentum.
            # Prefer yes_ask_dollars (taker entry price); fall back to suffixed variants,
            # then unsuffixed. Kalshi V2 API returns fields like yes_ask_dollars.
            open_yes = (
                MicroOptimizations.fast_normalize_price(market.get("yes_ask_dollars"))
                or MicroOptimizations.fast_normalize_price(market.get("yes_ask"))
                or MicroOptimizations.fast_normalize_price(
                    market.get("yes_bid_dollars")
                )
                or MicroOptimizations.fast_normalize_price(market.get("yes_bid"))
                or MicroOptimizations.fast_normalize_price(
                    market.get("yes_price_dollars")
                )
                or MicroOptimizations.fast_normalize_price(market.get("yes_price"))
                or MicroOptimizations.fast_normalize_price(
                    market.get("last_price_dollars")
                )
                or MicroOptimizations.fast_normalize_price(market.get("last_price"))
            )
            if open_yes is not None and 0.0 < open_yes <= 1.0:
                self._window_open_yes_price = open_yes
                print(
                    f"[{self.asset}] IWMC window_open_yes_price captured: ${open_yes:.3f}"
                )
            else:
                print(
                    f"[{self.asset}] IWMC WARNING: could not capture window_open_yes_price from market data: "
                    f"yes_ask={market.get('yes_ask')} yes_ask_dollars={market.get('yes_ask_dollars')} "
                    f"yes_bid={market.get('yes_bid')} yes_bid_dollars={market.get('yes_bid_dollars')}"
                )
            print(
                f"[{self.asset}] Fetched initial market prices: yes_bid={market.get('yes_bid')} yes_ask={market.get('yes_ask')} "
                f"yes_bid_dollars={market.get('yes_bid_dollars')} yes_ask_dollars={market.get('yes_ask_dollars')}"
            )
        except Exception as e:
            print(f"[{self.asset}] Failed to fetch initial market prices: {e}")

    async def handle_ticker(self, data):
        try:
            return await self._handle_ticker_inner(data)
        except Exception as exc:
            print(f"[{self.asset}] ticker handler crashed: {exc}")
            import traceback

            traceback.print_exc()

    async def _on_futures_tick(self, spot_price):
        if not getattr(self, "latest_ticker_data", None):
            return

        try:
            # Latency Arbitrage: evaluate market state instantly on Binance tick!
            await self._handle_ticker_inner(self.latest_ticker_data)
        except Exception as exc:
            print(f"[{self.asset}] futures tick handler crashed: {exc}")

    async def _handle_ticker_inner(self, data):
        msg_type = data.get("type")
        if msg_type == "orderbook_snapshot":
            msg = data.get("msg", {})
            self.l2_bids = {p: q for p, q in msg.get("bids", [])}
            self.l2_asks = {p: q for p, q in msg.get("asks", [])}
            print(
                f"[{self.asset}] ORDERBOOK SNAPSHOT: l2b={len(self.l2_bids)} l2a={len(self.l2_asks)}"
            )
            return  # Don't reach signal evaluation for orderbook msgs
        elif msg_type == "orderbook_delta":
            msg = data.get("msg", {})
            for p, q in msg.get("bids", []):
                if q == 0:
                    self.l2_bids.pop(p, None)
                else:
                    self.l2_bids[p] = q
            for p, q in msg.get("asks", []):
                if q == 0:
                    self.l2_asks.pop(p, None)
                else:
                    self.l2_asks[p] = q
            return  # Don't reach signal evaluation for orderbook msgs

        if msg_type != "ticker":
            return

        self.latest_ticker_data = data

        ticker_data = data.get("data") or data.get("msg") or {}
        ticker = (
            ticker_data.get("market_ticker")
            or ticker_data.get("ticker")
            or ticker_data.get("event_ticker")
        )
        if ticker and ticker != self.current_ticker:
            return

        # print(f"[{self.asset}] Processing ticker...") # Removed to prevent terminal freezing
        # NOTE: We do NOT force IDLE every tick. The state machine tracks whether
        # we're in a position. sync_current_position() handles state transitions
        # from the API. Forcing IDLE would break exit logic and allow duplicates.

        if self.market_expired():
            # Record settlement for IWMC strategy ONCE when market expires.
            # Only record THIS asset's settlement — each EventLoop records its own.
            if (
                hasattr(self, "iwmc_manager")
                and self.iwmc_manager
                and not getattr(self, "_iwmc_settlement_recorded", False)
            ):
                try:
                    ticker_data = data.get("data") or data.get("msg") or {}
                    settlement_price = None
                    for key in [
                        "yes_price_dollars",
                        "yes_price",
                        "last_price_dollars",
                        "last_price",
                        "yes_ask_dollars",
                        "yes_bid_dollars",
                        "settlement_price",
                    ]:
                        if key in ticker_data and ticker_data[key] is not None:
                            settlement_price = float(ticker_data[key])
                            break

                    if (
                        settlement_price is not None
                        and 0.0 < settlement_price <= 1.0
                        and self.strike is not None
                    ):
                        window_open = self._window_open_yes_price
                        if window_open is not None and 0.0 < window_open <= 1.0:
                            # Record ONLY this asset's settlement, not all assets.
                            # Each EventLoop records its own; the shared manager stores per-asset.
                            self.iwmc_manager.record_settlement(
                                asset=self.asset,
                                window_start=int(self.expiry - 900)
                                if self.expiry
                                else int(time.time() - 900),
                                window_end=int(self.expiry)
                                if self.expiry
                                else int(time.time()),
                                settlement_price=settlement_price,
                                window_open_price=window_open,
                            )
                            print(
                                f"[{self.asset}] IWMC settlement: momentum={(settlement_price - window_open) / window_open:.4f}, "
                                f"settle=${settlement_price:.2f}, open=${window_open:.2f}"
                            )
                        else:
                            print(
                                f"[{self.asset}] IWMC settlement skipped: invalid window_open_yes_price={window_open}"
                            )
                    else:
                        print(
                            f"[{self.asset}] IWMC settlement skipped: settlement_price={settlement_price}"
                        )

                    # Mark recorded so we don't spam on every tick
                    self._iwmc_settlement_recorded = True

                except Exception as e:
                    print(f"[{self.asset}] IWMC settlement recording failed: {e}")
                    self._iwmc_settlement_recorded = True

            await self.sync_current_position()
            now_ts = int(time.time())
            if (
                not hasattr(self, "_last_expired_print_ts")
                or now_ts - self._last_expired_print_ts >= 15
            ):
                print(
                    f"[{self.asset}] Market expired. Resetting memory position and state to IDLE for settlement."
                )
                self._last_expired_print_ts = now_ts
            old_exposure = self.risk.get_asset_exposure(self.asset)
            if old_exposure > 0:
                if (
                    not hasattr(self, "_last_expired_clear_ts")
                    or now_ts - self._last_expired_clear_ts >= 15
                ):
                    print(
                        f"[{self.asset}] Clearing exposure of ${old_exposure:.2f} for expired market"
                    )
                    self._last_expired_clear_ts = now_ts
                self.risk.set_asset_exposure(self.asset, 0.0)
            self.position.close()
            self.state.exit()

            # Cancel ALL open resting orders on market expiry so nothing is left in Kalshi queue
            for side in ["yes", "no"]:
                oid = getattr(self, "resting_order_ids", {}).get(side)
                if oid:
                    try:
                        await self._maybe_cancel(oid)
                        print(
                            f"[{self.asset}] Market expired — canceled resting {side} order: {oid}"
                        )
                    except Exception as e:
                        print(
                            f"[{self.asset}] Market expired — failed to cancel resting {side} order {oid}: {e}"
                        )
                tp_oid = getattr(self, "resting_sell_orders", {}).get(side)
                if tp_oid:
                    try:
                        await self._maybe_cancel(tp_oid)
                        print(
                            f"[{self.asset}] Market expired — canceled resting TP {side} order: {tp_oid}"
                        )
                    except Exception as e:
                        print(
                            f"[{self.asset}] Market expired — failed to cancel resting TP {side} order {tp_oid}: {e}"
                        )

            self.resting_order_ids = {"yes": None, "no": None}
            self.resting_order_prices = {"yes": 0.0, "no": 0.0}
            self.resting_order_contracts = {"yes": 0, "no": 0}
            self.resting_sell_orders = {"yes": None, "no": None}
            self.take_profit_placed = False

            print(
                f"[{self.asset}] Ignoring expired market ticker:", self.current_ticker
            )
            return

        if not self._debugged_first_ticker:
            print(f"[{self.asset}] First ticker payload:", data)
            from config import LOG_RAW_TICKER_KEYS

            if LOG_RAW_TICKER_KEYS:
                print(
                    f"[{self.asset}] Raw ticker_data keys: {sorted(ticker_data.keys())}"
                )
                # Dump all key-value pairs for debugging
                for k, v in sorted(ticker_data.items()):
                    print(f"  {k} = {v}")
            self._debugged_first_ticker = True

        # Only evaluate signals or place orders if the market is active
        market_status = ticker_data.get("status")
        if market_status is not None and market_status != "active":
            now_ts = int(time.time())
            if (
                not hasattr(self, "_last_status_print_ts")
                or now_ts - self._last_status_print_ts >= 15
            ):
                print(
                    f"[{self.asset}] Market {self.current_ticker} is not active (status: {market_status}). Waiting for active status..."
                )
                self._last_status_print_ts = now_ts
            return

        await self.sync_current_position()

        yes_price = MicroOptimizations.fast_normalize_price(
            ticker_data.get("yes_price")
            or ticker_data.get("yes_price_dollars")
            or ticker_data.get("yes_ask_dollars")
            or ticker_data.get("yes_bid_dollars")
            or ticker_data.get("yes_price_cents")
        )
        no_price = MicroOptimizations.fast_normalize_price(
            ticker_data.get("no_price")
            or ticker_data.get("no_price_dollars")
            or ticker_data.get("no_ask_dollars")
            or ticker_data.get("no_bid_dollars")
            or ticker_data.get("no_price_cents")
        )

        if no_price is None and yes_price is not None:
            if 0.0 <= yes_price <= 1.0:
                no_price = 1.0 - yes_price
        if yes_price is None and no_price is not None:
            if 0.0 <= no_price <= 1.0:
                yes_price = 1.0 - no_price

        if yes_price is not None and yes_price > 0:
            _now = time.time()
            self._yes_price_history.append((_now, yes_price))
            self._yes_price_history = [
                (t, p) for t, p in self._yes_price_history if _now - t <= 90
            ]
            if len(self._yes_price_history) >= 2:
                oldest_t, oldest_p = self._yes_price_history[0]
                elapsed = _now - oldest_t
                if elapsed > 0 and oldest_p > 0:
                    self._yes_price_velocity = (
                        (yes_price - oldest_p) / oldest_p * 10000
                    ) / elapsed  # bps/sec
            else:
                self._yes_price_velocity = 0.0

        yes_bid = MicroOptimizations.fast_normalize_price(
            ticker_data.get("yes_bid") or ticker_data.get("yes_bid_dollars")
        )
        yes_ask = MicroOptimizations.fast_normalize_price(
            ticker_data.get("yes_ask") or ticker_data.get("yes_ask_dollars")
        )
        if yes_bid is None:
            yes_bid = yes_price
        if yes_ask is None:
            yes_ask = yes_price

        no_bid = MicroOptimizations.fast_normalize_price(
            ticker_data.get("no_bid") or ticker_data.get("no_bid_dollars")
        )
        if no_bid is None and yes_ask is not None:
            no_bid = 1.0 - yes_ask
        if no_bid is None:
            no_bid = no_price

        no_ask = MicroOptimizations.fast_normalize_price(
            ticker_data.get("no_ask") or ticker_data.get("no_ask_dollars")
        )
        if no_ask is None and yes_bid is not None:
            no_ask = 1.0 - yes_bid
        if no_ask is None:
            no_ask = no_price

        bid_size = float(
            ticker_data.get("yes_bid_size")
            or ticker_data.get("yes_bid_size_fp")
            or ticker_data.get("bid_size")
            or 0
        )
        ask_size = float(
            ticker_data.get("yes_ask_size")
            or ticker_data.get("yes_ask_size_fp")
            or ticker_data.get("ask_size")
            or 0
        )

        # print("Ticker yes/no prices after normalization:", yes_price, no_price) # Removed to prevent terminal freezing

        if yes_price is None and no_price is None:
            print("Ticker missing yes/no prices:", ticker_data)
            return

        spot = self.futures.get_spot()
        if not spot:
            return

        # Record spot price for IWMC volatility estimation (once per minute)
        now_ts = time.time()
        self._spot_price_history.append((now_ts, spot))
        # Keep only last 60 minutes
        self._spot_price_history = [
            (t, p) for t, p in self._spot_price_history if now_ts - t <= 3600
        ]
        # Keep only 1 price per minute for volatility calc
        minute_buckets = {}
        for t, p in self._spot_price_history:
            minute = int(t)
            if minute not in minute_buckets:
                minute_buckets[minute] = p
        spot_prices_for_iwmc = list(minute_buckets.values())[-60:]

        # Initialize strike if it is None (e.g. for Up or Down markets where strike is TBD at discovery)
        if self.strike is None:
            self.strike = spot
            print(
                f"[{self.asset}] Strike was None/TBD; initialized to current spot price: {self.strike}"
            )

        # PRIMARY SIGNAL: Coinbase 1-min velocity momentum (not Binance)
        # Research: 53.74% win rate, +$19,451 on BTC alone (TurbineFi, window ending May 4, 2026)
        if self.coinbase:
            cb_velocity_bps = self.coinbase.get_momentum_bps(window_seconds=60)
            move_pct = cb_velocity_bps / 10000 if cb_velocity_bps is not None else 0.0
        else:
            cb_velocity_bps = None
            move_pct = self.futures.get_recent_move_pct()

        time_remaining = self.expiry - int(time.time())

        # Pre-discover the next market in the last 120s so rollover is instant.
        # Set the flag BEFORE the task so we only fire one.
        if (
            not getattr(self, "_pre_discovery_done", False)
            and time_remaining <= 120
            and time_remaining > 0
        ):
            self._pre_discovery_done = True  # prevent duplicate tasks
            asyncio.create_task(self._pre_discover_market())

        # Only print state every 5 seconds to avoid freezing the terminal
        now_ts = int(time.time())
        if not hasattr(self, "_last_print_ts") or now_ts - self._last_print_ts >= 5:
            print(
                f"[{self.asset}] move_pct={move_pct}, time_remaining={time_remaining}, state={self.state.state}"
            )
            self._last_print_ts = now_ts

        # HYBRID LOGIC: Maker (Stink Bids) + Taker (Sniper)
        if self.state.can_enter():
            # ==========================================
            # 4. TAKER LOGIC: Directional Sniper
            # ==========================================
            if not getattr(self, "_order_in_flight", False):
                try:
                    multiplier = self.risk.calculate_multiplier(spot, self.strike)
                except Exception:
                    multiplier = 1.0

                # Bid/Ask variables hoisted to global tick scope

                # --- SIGNAL DEBOUNCER ---
                current_time = time.time()
                last_sig_time = getattr(self, "last_signal_time", 0)
                last_spot = getattr(self, "last_spot_price", 0)

                spot_moved_bps = (
                    abs((spot - last_spot) / last_spot * 10000)
                    if last_spot > 0
                    else 100
                )
                if (current_time - last_sig_time < 5) and (spot_moved_bps < 5):
                    signal_result = (None, None)
                else:
                    self.last_signal_time = current_time
                    self.last_spot_price = spot

                    pass

                    signal_result = self.signal.evaluate(
                        asset_name=self.asset,
                        bid=yes_bid,
                        ask=yes_ask,
                        bid_size=bid_size,
                        ask_size=ask_size,
                        strike=self.strike,
                        spot_price=spot,
                        multiplier=multiplier,
                        time_remaining=time_remaining,
                        recent_move_pct=move_pct,
                        futures_trend=self.futures.get_trend_direction(),
                    )

                if isinstance(signal_result, tuple):
                    signal, win_prob = signal_result
                else:
                    signal = signal_result
                    win_prob = 0.55

                # --- IWMC: Inter-Window Momentum Carry Strategy ---
                # Evaluate IWMC signal in first minute of new window (time_remaining > 840).
                # Route through self.iwmc_manager, NOT self.iwmc: the manager's instances share
                # the settlement history recorded on window expiry, while the standalone
                # self.iwmc instance has empty history and would never produce a signal.
                iwmc_signal = None
                if (
                    time_remaining > 840
                    and getattr(self, "iwmc_manager", None) is not None
                ):
                    iwmc_signal = self.iwmc_manager.get_signal(
                        asset=self.asset,
                        current_kalshi_price=yes_ask
                        if yes_ask is not None
                        else yes_price,
                        strike_price=self.strike,
                        spot_prices=spot_prices_for_iwmc,
                        time_remaining=time_remaining,
                    )
                    if iwmc_signal:
                        print(
                            f"[IWMC] {self.asset} signal: {iwmc_signal['direction']} | "
                            f"conf={iwmc_signal['confidence']:.2f} | "
                            f"source={iwmc_signal['source_asset']} | "
                            f"deviation={iwmc_signal['deviation']:.4f}"
                        )
                        # IWMC signal takes priority if high confidence
                        if iwmc_signal["confidence"] > 0.5:
                            signal = iwmc_signal["direction"]
                            win_prob = iwmc_signal["confidence"]
                            print(
                                f"[IWMC] {self.asset} OVERRIDE: {signal} with win_prob={win_prob:.2f}"
                            )

                # --- CRMD: CFB RTI Momentum Divergence Strategy ---
                # Evaluate CRMD signal in final 180s (time_remaining < 180 and > 60).
                # Independent of IWMC_ONLY_MODE — fires alongside IWMC or normal signals.
                from config import ENABLE_CRMD

                crmd_signal = None
                if ENABLE_CRMD and self.strike is not None and self.strike > 0:
                    crmd_signal = self.crmd.evaluate(
                        strike=self.strike,
                        kalshi_yes_price=yes_ask if yes_ask is not None else yes_price,
                        time_remaining=time_remaining,
                    )
                    if crmd_signal:
                        print(
                            f"[CRMD] {self.asset} signal: {crmd_signal['direction']} | "
                            f"conf={crmd_signal['confidence']:.2f} | "
                            f"rti_mom={crmd_signal['rti_momentum_bps']:+.3f} bps/s"
                        )
                        # CRMD signal takes priority if high confidence
                        if crmd_signal["confidence"] > 0.4:
                            signal = crmd_signal["direction"]
                            win_prob = crmd_signal["confidence"]
                            print(
                                f"[CRMD] {self.asset} OVERRIDE: {signal} with win_prob={win_prob:.2f}"
                            )

                # --- SKEW_FADE: Orderbook Skew Fade Strategy ---
                # Evaluate SKEW_FADE signal in the 840s–180s window (14–3 minutes remaining).
                # Independent of IWMC_ONLY_MODE — fires alongside IWMC/CRMD or normal signals.
                from config import ENABLE_SKEW_FADE, SKEW_FADE_CONFIDENCE_THRESHOLD

                skew_signal = None
                if (
                    ENABLE_SKEW_FADE
                    and hasattr(self, "skew_fade")
                    and self.strike is not None
                    and self.strike > 0
                    and 180 <= time_remaining <= 840
                ):
                    skew_signal = self.skew_fade.evaluate(
                        l2_bids=self.l2_bids,
                        l2_asks=self.l2_asks,
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        time_remaining=time_remaining,
                    )
                    if skew_signal:
                        print(
                            f"[SKEW_FADE] {self.asset} signal: {skew_signal['direction']} | "
                            f"conf={skew_signal['confidence']:.2f} | "
                            f"yes_ratio={skew_signal['yes_ratio']:.2%} | "
                            f"total_depth={skew_signal['yes_depth'] + skew_signal['no_depth']:.0f}"
                        )
                        # SKEW_FADE signal takes priority if high confidence
                        if skew_signal["confidence"] >= SKEW_FADE_CONFIDENCE_THRESHOLD:
                            signal = skew_signal["direction"]
                            win_prob = skew_signal["confidence"]
                            print(
                                f"[SKEW_FADE] {self.asset} OVERRIDE: {signal} with win_prob={win_prob:.2f}"
                            )

                # --- FEATURE 4: L2 ORDERBOOK IMBALANCE ---
                l2_bid_depth = sum(getattr(self, "l2_bids", {}).values())
                l2_ask_depth = sum(getattr(self, "l2_asks", {}).values())

                l2_boost = 0.0
                if l2_bid_depth + l2_ask_depth > 0:
                    if signal == "ENTER_YES":
                        l2_ratio = l2_bid_depth / (l2_ask_depth + 1)
                        if l2_ratio > 1.5:
                            l2_boost = 0.05
                        elif l2_ratio < 0.5:
                            l2_boost = -0.05
                    elif signal == "ENTER_NO":
                        l2_ratio = l2_ask_depth / (l2_bid_depth + 1)
                        if l2_ratio > 1.5:
                            l2_boost = 0.05
                        elif l2_ratio < 0.5:
                            l2_boost = -0.05

                if l2_boost != 0.0 and signal:
                    print(
                        f"\033[96m[{self.asset}] L2 Depth: {l2_bid_depth} YES Bids / {l2_ask_depth} YES Asks. Applying {l2_boost:+.2f} edge boost!\033[0m"
                    )
                    win_prob += l2_boost

                kalshi_vel_boost = 0.0
                yes_vel = getattr(self, "_yes_price_velocity", 0.0)
                if abs(yes_vel) > 0.5:
                    if signal == "ENTER_YES" and yes_vel > 0:
                        kalshi_vel_boost = min(0.06, yes_vel * 0.04)
                    elif signal == "ENTER_NO" and yes_vel < 0:
                        kalshi_vel_boost = min(0.06, abs(yes_vel) * 0.04)
                    elif signal == "ENTER_YES" and yes_vel < -0.5:
                        kalshi_vel_boost = max(-0.06, yes_vel * 0.04)
                    elif signal == "ENTER_NO" and yes_vel > 0.5:
                        kalshi_vel_boost = max(-0.06, -yes_vel * 0.04)
                if kalshi_vel_boost != 0.0 and signal:
                    print(
                        f"\033[96m[{self.asset}] Kalshi price velocity: {yes_vel:.2f} bps/sec \u2192 {kalshi_vel_boost:+.3f} win_prob boost\033[0m"
                    )
                    win_prob = max(0.0, min(1.0, (win_prob or 0.55) + kalshi_vel_boost))

                regime = getattr(self.signal, "current_regime", "RANGE")

                if signal:
                    if ENABLE_EXECUTION_GUARDS:
                        live_side = (
                            "yes"
                            if signal == "ENTER_YES"
                            else ("no" if signal == "ENTER_NO" else None)
                        )
                        live_price = None
                        if live_side == "yes":
                            live_price = yes_ask if yes_ask is not None else yes_price
                        elif live_side == "no":
                            no_ask = MicroOptimizations.fast_normalize_price(
                                ticker_data.get("no_ask")
                                or ticker_data.get("no_ask_dollars")
                            )
                            if no_ask is None and yes_bid is not None:
                                no_ask = 1.0 - yes_bid
                            live_price = no_ask if no_ask is not None else no_price
                        live_entry = (
                            min(0.40, live_price + 0.01)
                            if live_price is not None
                            else 0.01
                        )
                        current_contracts = (
                            self.position.contracts
                            if getattr(self, "position", None)
                            else 0
                        )
                        live_size = (
                            self.risk.calculate_contracts(
                                price=live_entry,
                                asset_name=self.asset,
                                multiplier=multiplier,
                                recent_pnl_pct=self.risk.get_recent_performance(),
                                win_prob=win_prob,
                                regime=regime,
                                current_open_contracts=current_contracts,
                                portfolio_positions_ref=getattr(
                                    self, "_portfolio_positions_ref", None
                                ),
                            )
                            if live_side and live_entry <= 0.40
                            else 0
                        )

                        self.execution_guard.log_opportunity(
                            market=self.current_ticker,
                            live_score=win_prob if signal else None,
                            live_regime=regime,
                            live_side=live_side,
                            live_size=live_size,
                            order_type="quick",
                            entry_price=live_entry if live_side else 0.0,
                            exit_plan=f"TP: {TAKE_PROFIT_PCT * 100}%, SL: {STOP_LOSS_PCT * 100}%",
                        )

                if signal:
                    from config import ENABLE_MAKER_REGIME, ENABLE_SNIPER_REGIME

                    # ENTER_YES = LONG YES = buy YES, ENTER_NO = SHORT YES = sell YES
                    is_long = signal == "ENTER_YES"
                    action = "WAIT"
                    order_type = None
                    price = None
                    entry_side = "yes"
                    kw_action = "buy" if is_long else "sell"

                    # One-direction-per-window: block opposite-side signals on same ticker
                    signal_side = "yes" if is_long else "no"
                    first_dir = self._window_trade_direction.get(self.current_ticker)
                    if first_dir is not None and signal_side != first_dir:
                        print(
                            f"[{self.asset}] ONE-DIRECTION BLOCK: Already traded {first_dir.upper()} on {self.current_ticker}. Blocking opposite signal."
                        )
                        signal = None

                    # Post-trade cooldown: block re-entry for N seconds after ANY trade exit
                    if signal and time.time() < self._post_trade_cooldown_until:
                        remaining_cooldown = int(
                            self._post_trade_cooldown_until - time.time()
                        )
                        print(
                            f"[{self.asset}] POST-TRADE COOLDOWN: {remaining_cooldown}s remaining. Skipping entry."
                        )
                        signal = None

                    # SHOCK regime hard halt
                    if signal and regime == "SHOCK":
                        print(
                            f"[{self.asset}] Regime SHOCK: Hard halt — no trading during shock regime."
                        )
                        signal = None

                    # Macro trend filter: skip counter-trend entries
                    trend = getattr(self.signal, "current_trend_strength", 0.0)
                    if is_long and trend < -0.001:
                        print(
                            f"[{self.asset}] TREND FILTER: skipping buy YES in downtrend (trend={trend:.4f})"
                        )
                        signal = None
                    elif not is_long and trend > 0.001:
                        print(
                            f"[{self.asset}] TREND FILTER: skipping sell YES in uptrend (trend={trend:.4f})"
                        )
                        signal = None

                    # Calculate entry price
                    ask_price = yes_ask if yes_ask is not None else yes_price
                    bid_price = yes_bid if yes_bid is not None else spot

                    # Determine Regime Action
                    if regime == "RANGE":
                        if ENABLE_MAKER_REGIME:
                            # Use MARKET when signal aligns with momentum to ensure fills
                            momentum_aligns = (is_long and move_pct > 0) or (
                                not is_long and move_pct < 0
                            )
                            high_conf = win_prob is not None and win_prob > 0.85
                            if momentum_aligns or high_conf:
                                action = "EXECUTE"
                                order_type = "MARKET"
                                # Buy at ask+1c, sell at bid-1c to cross the spread aggressively
                                if is_long:
                                    price = (
                                        min(0.50, ask_price + 0.01)
                                        if ask_price is not None
                                        else 0.01
                                    )
                                else:
                                    price = (
                                        max(0.01, bid_price - 0.01)
                                        if bid_price is not None
                                        else 0.01
                                    )
                                reason = (
                                    "momentum alignment"
                                    if momentum_aligns
                                    else "high confidence"
                                )
                                print(
                                    f"[{self.asset}] Regime {regime}: {reason}. Routing to TAKER MARKET at ${price:.2f}"
                                )
                            else:
                                action = "EXECUTE"
                                order_type = "LIMIT"
                                price = bid_price if bid_price is not None else 0.01
                                print(
                                    f"[{self.asset}] Regime {regime}: Routing to MAKER LIMIT at ${price:.2f}"
                                )
                        else:
                            print(
                                f"[{self.asset}] Regime {regime} ignored (ENABLE_MAKER_REGIME is False)"
                            )

                    elif regime in ["TREND", "HIGH_VOL"]:
                        if ENABLE_SNIPER_REGIME:
                            proposed_price = (
                                min(0.50, ask_price + 0.01)
                                if ask_price is not None
                                else 0.01
                            )
                            ev = (
                                self.risk.calculate_expected_value(
                                    win_prob, proposed_price
                                )
                                if win_prob is not None
                                else -1.0
                            )
                            if ev >= 0.005:
                                action = "EXECUTE"
                                order_type = "MARKET"
                                price = proposed_price
                                print(
                                    f"[{self.asset}] Regime {regime}: EV is ${ev:.3f}. Routing to TAKER MARKET at ${price:.2f}"
                                )
                            else:
                                print(
                                    f"[{self.asset}] Regime {regime}: EV ${ev if ev is not None else 0:.3f} too low. WAIT."
                                )
                        else:
                            print(
                                f"[{self.asset}] Regime {regime} ignored (ENABLE_SNIPER_REGIME is False)"
                            )

                    elif regime == "SHOCK":
                        print(f"[{self.asset}] Regime SHOCK: Routing to WAIT.")

                    if price is not None and price > 0.65 and is_long:
                        print(
                            f"[{self.asset}] Entry price ${price:.2f} exceeds $0.65 cap for YES entry. Routing to WAIT."
                        )
                        action = "WAIT"

                    if (
                        time_remaining is not None
                        and time_remaining < NO_ENTRY_LAST_SECONDS
                    ):
                        if time_remaining >= 10:
                            print(
                                f"[{self.asset}] Less than {NO_ENTRY_LAST_SECONDS}s remaining but time_remaining={time_remaining}s > 10s — allowing"
                            )
                        else:
                            print(
                                f"[{self.asset}] Less than {NO_ENTRY_LAST_SECONDS}s remaining. Theta decay lockout."
                            )
                            action = "WAIT"

                    if (
                        action == "EXECUTE"
                        and order_type == "LIMIT"
                        and hasattr(self, "resting_order_ids")
                        and self.resting_order_ids.get("yes")
                    ):
                        print(
                            f"[{self.asset}] Already has resting order — skipping duplicate LIMIT on {self.current_ticker}"
                        )
                        action = "WAIT"

                    # Post-stop-loss cooldown: prevent immediate re-entry on the same volatile candle
                    if action == "EXECUTE":
                        from config import STOP_LOSS_COOLDOWN_SECONDS

                        cooldown_remaining = (
                            self._stop_loss_cooldown_until - time.time()
                        )
                        if cooldown_remaining > 0:
                            print(
                                f"[{self.asset}] STOP-LOSS COOLDOWN: {cooldown_remaining:.0f}s remaining. Skipping entry."
                            )
                            action = "WAIT"

                    if action == "EXECUTE" and price is not None:
                        current_contracts = (
                            self.position.contracts
                            if getattr(self, "position", None)
                            else 0
                        )
                        contracts = self.risk.calculate_contracts(
                            price=price,
                            asset_name=self.asset,
                            multiplier=multiplier,
                            recent_pnl_pct=self.risk.get_recent_performance(),
                            win_prob=win_prob,
                            regime=regime,
                            current_open_contracts=current_contracts,
                            portfolio_positions_ref=getattr(
                                self, "_portfolio_positions_ref", None
                            ),
                        )

                        _exposure_reserved = False
                        if contracts > 0:
                            exposure_needed = price * contracts
                            if not self.risk.reserve_exposure(
                                self.asset, exposure_needed
                            ):
                                print(
                                    f"[{self.asset}] Pre-trade reservation blocked: ${exposure_needed:.2f} exceeds available cap"
                                )
                                contracts = 0
                            else:
                                _exposure_reserved = True
                                print(
                                    f"[{self.asset}] Pre-trade reservation OK: ${exposure_needed:.2f} for {contracts} contracts"
                                )

                        if contracts > 0 and ENABLE_EXECUTION_GUARDS:
                            safe, reason = self.execution_guard.evaluate_execution(
                                ticker=self.current_ticker,
                                bid=yes_bid,
                                ask=yes_ask,
                                bid_size=bid_size,
                                ask_size=ask_size,
                            )
                            if not safe and order_type != "LIMIT":
                                print(f"[{self.asset}] Execution Guard BLOCK: {reason}")
                                contracts = 0
                            elif not safe and order_type == "LIMIT":
                                print(
                                    f"[{self.asset}] Execution Guard BYPASSED: {reason} (Limit Orders allowed on thin books)"
                                )

                        if contracts == 0 and _exposure_reserved:
                            self.risk.release_exposure(self.asset)
                            _exposure_reserved = False

                        if contracts > 0:
                            try:
                                self._order_in_flight = True
                                print(
                                    f"[{self.asset}] Routing Triggered! Signal: {signal}, type: {order_type}, executing entry {kw_action} YES at ${price:.2f}"
                                )

                                if order_type == "MARKET":
                                    order_result = await self._maybe_place_market(
                                        ticker=self.current_ticker,
                                        side=entry_side,
                                        contracts=contracts,
                                        price=price,
                                        action=kw_action,
                                    )
                                else:
                                    client_oid = (
                                        f"maker_{self.asset}_{int(time.time())}"
                                    )
                                    # Snap to Kalshi's valid price grid
                                    price = self._snap_price(price)
                                    order_result = await self._maybe_place_limit(
                                        ticker=self.current_ticker,
                                        side=entry_side,
                                        contracts=contracts,
                                        price=price,
                                        action=kw_action,
                                        client_order_id=client_oid,
                                    )

                                if ENABLE_EXECUTION_GUARDS:
                                    self.execution_guard.record_execution(
                                        self.current_ticker
                                    )

                                # Process fill / rest
                                order_details = (
                                    order_result.get("order") or order_result
                                    if isinstance(order_result, dict)
                                    else {}
                                )
                                raw_fill = (
                                    order_details.get("fill_count")
                                    or order_details.get("fill_count_fp")
                                    or order_details.get("filled_count")
                                    or order_details.get("filled_contracts")
                                )

                                filled_contracts = 0
                                if raw_fill is not None:
                                    try:
                                        filled_contracts = int(float(raw_fill))
                                    except Exception:
                                        filled_contracts = 0
                                else:
                                    if order_type == "MARKET":
                                        filled_contracts = contracts
                                    else:
                                        filled_contracts = 0
                                if filled_contracts > 0:
                                    # Capture actual fill price and fee from Kalshi API
                                    avg_fill_price = float(
                                        order_details.get("average_fill_price") or price
                                    )
                                    entry_fee_usd = (
                                        float(
                                            order_details.get("average_fee_paid") or 0
                                        )
                                        * filled_contracts
                                    )
                                    self._last_entry_fee = entry_fee_usd
                                    exposure_increase = (
                                        avg_fill_price * filled_contracts
                                    )
                                    self.risk.confirm_exposure(
                                        self.asset, exposure_increase
                                    )

                                    self.position.open(
                                        avg_fill_price,
                                        filled_contracts,
                                        entry_side,
                                        position_type=kw_action,
                                    )
                                    self.state.enter(signal)
                                    self._entry_timestamp = time.time()
                                    self._entry_time_iso = datetime.utcnow().isoformat()
                                    logical_entry_side = (
                                        "no" if kw_action == "sell" else "yes"
                                    )
                                    self._window_trade_direction[
                                        self.current_ticker
                                    ] = logical_entry_side
                                    print(
                                        f"\033[92m[{self.asset}] EXECUTED: {filled_contracts} contracts at ${avg_fill_price:.2f} (fee=${entry_fee_usd:.3f})\033[0m"
                                    )
                                elif order_type == "LIMIT":
                                    if _exposure_reserved:
                                        self.risk.release_exposure(self.asset)
                                    oid = order_details.get("order_id")
                                    if oid:
                                        if not hasattr(self, "resting_order_ids"):
                                            self.resting_order_ids = {
                                                "yes": None,
                                                "no": None,
                                            }
                                            self.resting_order_prices = {
                                                "yes": 0.0,
                                                "no": 0.0,
                                            }
                                            self.resting_order_contracts = {
                                                "yes": 0,
                                                "no": 0,
                                            }
                                        self.resting_order_ids[entry_side] = oid
                                        self.resting_order_prices[entry_side] = price
                                        self.resting_order_contracts[entry_side] = (
                                            contracts
                                        )
                                    print(
                                        f"\033[93m[{self.asset}] RESTING: {contracts} contracts at ${price:.2f}\033[0m"
                                    )
                                else:
                                    if _exposure_reserved:
                                        self.risk.release_exposure(self.asset)

                            except Exception as e:
                                if _exposure_reserved:
                                    self.risk.release_exposure(self.asset)
                                print(f"[{self.asset}] Routing order failed: {e}")
                            finally:
                                self._order_in_flight = False

        # EXIT LOGIC (HYBRID)
        else:
            if getattr(self.position, "hold_to_expiry", False):
                # Do nothing, let it expire
                if time_remaining > 0 and time_remaining % 10 == 0:
                    print(
                        f"[{self.asset}] Holding TWAP Arb position to expiry. {time_remaining}s left."
                    )
                return

            from config import ENABLE_NEW_EXITS

            # --- NEW ACTIVE EXIT EVALUATION ---
            if (
                ENABLE_NEW_EXITS
                and getattr(self.position, "entry_price", None) is not None
            ):
                self.exit_engine.sync_position(
                    self.position.entry_price,
                    getattr(self.position, "contracts", 0),
                    getattr(self.position, "side", "yes"),
                    getattr(self.position, "entry_time", time.time()),
                    getattr(self.position, "peak_pnl", 0.0),
                    position_type=getattr(self.position, "position_type", "buy"),
                    asset=self.asset,
                )

                # Determine current market value of our holding
                pt = getattr(self.position, "position_type", "buy")
                if pt == "sell":
                    # Short YES: we need to buy back — value is the ask price
                    current_value = yes_ask if yes_ask is not None else yes_price
                else:
                    # Long YES: we can sell — value is the bid price
                    current_value = yes_bid if yes_bid is not None else yes_price
                if current_value is None:
                    current_value = no_bid if pt == "sell" else no_bid
                    if current_value is None:
                        current_value = no_price

                if current_value is not None and self.position.entry_price > 0:
                    # Update peak PnL in position manager manually since we bypassed it
                    raw_pnl = (
                        current_value - self.position.entry_price
                    ) / self.position.entry_price
                    # Invert for short positions: profit when price drops
                    actual_pnl = -raw_pnl if pt == "sell" else raw_pnl
                    if actual_pnl > getattr(self.position, "peak_pnl", 0.0):
                        self.position.peak_pnl = actual_pnl

                    exit_action, exit_reason = self.exit_engine.evaluate(
                        current_price=current_value,
                        current_bid=yes_bid if pt == "buy" else no_bid,
                        current_ask=yes_ask if pt == "buy" else no_ask,
                        liquidity_depth=bid_size
                        if self.position.side == "yes"
                        else ask_size,
                        time_remaining=time_remaining,
                        regime=getattr(self.signal, "current_regime", "RANGE"),
                    )

                    if exit_action in ("EXIT", "EXIT_50"):
                        if (
                            getattr(self, "_exit_in_flight", False)
                            or not self.position.side
                        ):
                            return
                        # Cooldown: don't retry the same exit for 5 seconds
                        last = getattr(self, "_last_exit_attempt", 0)
                        if time.time() - last < 5:
                            return

                        # Minimum hold time before hard/trailing stops can trigger
                        # (prevents instant catastrophic exits on thin book slippage)
                        # End-of-window exits are exempt — they always fire
                        if exit_reason not in ("end_of_window", "deep_itm"):
                            from config import MIN_HOLD_BEFORE_STOP_SECONDS

                            held_seconds = time.time() - getattr(
                                self, "_entry_timestamp", 0
                            )
                            if held_seconds < MIN_HOLD_BEFORE_STOP_SECONDS:
                                return  # silently skip — will retry next tick after threshold

                        self._last_exit_attempt = time.time()
                        self._exit_in_flight = True

                        print(
                            f"\033[91m[{self.asset}] 🚨 ACTIVE EXIT TRIGGERED: {exit_reason} at {current_value:.2f}\033[0m"
                        )

                        # 1. Cancel resting take-profit limit order if it exists
                        # Key by "yes" (TP is always a sell order on YES side regardless of position direction)
                        tp_oid = getattr(self, "resting_sell_orders", {}).get("yes")
                        if tp_oid:
                            try:
                                await self._maybe_cancel(tp_oid)
                                print(
                                    f"[{self.asset}] Exit: canceled resting TP order: {tp_oid}"
                                )
                            except Exception as e:
                                print(
                                    f"[{self.asset}] Exit: failed to cancel TP order {tp_oid}: {e}"
                                )
                            self.resting_sell_orders["yes"] = None

                        # Set cooldown on hard stops to prevent immediate re-entry
                        if exit_reason in ("hard_stop", "trailing_stop"):
                            from config import STOP_LOSS_COOLDOWN_SECONDS

                            self._stop_loss_cooldown_until = (
                                time.time() + STOP_LOSS_COOLDOWN_SECONDS
                            )
                            print(
                                f"[{self.asset}] Stop-loss cooldown activated for {STOP_LOSS_COOLDOWN_SECONDS}s"
                            )

                        # Compute aggressive LIMIT exit price
                        pt = getattr(self.position, "position_type", "buy")
                        close_action = "buy" if pt == "sell" else "sell"
                        if pt == "sell":
                            # Short: buy back YES — pay up to ask+1c to cross spread
                            aggressive_price = (
                                min(0.99, yes_ask + 0.01)
                                if yes_ask is not None
                                else 0.99
                            )
                        else:
                            # Long: sell YES — accept down to bid-1c to cross spread
                            aggressive_price = (
                                max(0.01, yes_bid - 0.01)
                                if yes_bid is not None
                                else 0.01
                            )

                        # 2a. EXIT_50: sell half the position
                        if exit_action == "EXIT_50":
                            if self.position.contracts <= 0:
                                print(
                                    f"[{self.asset}] EXIT_50: no contracts (contracts={self.position.contracts})"
                                )
                                self._exit_in_flight = False
                                return
                            half = max(1, self.position.contracts // 2)
                            filled, fee, avg_fill = await self._execute_exit(
                                contracts=half,
                                close_action=close_action,
                                aggressive_price=aggressive_price,
                                fallback_value=current_value,
                            )
                            if filled > 0:
                                self.position.contracts -= filled
                                print(
                                    f"[{self.asset}] Closed {filled} of {self.position.contracts + filled} contracts (50% time stop)"
                                )
                            else:
                                print(
                                    f"[{self.asset}] EXIT_50: order for {half} contracts did not fill"
                                )
                            self._exit_in_flight = False
                            return

                        # 2b. Full exit: close position
                        if self.position.contracts > 0:
                            filled, fee, avg_fill = await self._execute_exit(
                                contracts=self.position.contracts,
                                close_action=close_action,
                                aggressive_price=aggressive_price,
                                fallback_value=current_value,
                            )
                            if filled > 0:
                                self.position.contracts -= filled
                                print(
                                    f"[{self.asset}] Closed {filled} contracts (full exit) avg_fill=${avg_fill:.2f} fee=${fee:.3f}"
                                )

                                # Persist trade to CSV via TradeLogger
                                if self.trade_logger:
                                    entry_price = (
                                        getattr(self.position, "entry_price", 0) or 0
                                    )
                                    pos_contracts = filled
                                    held_seconds = time.time() - getattr(
                                        self, "_entry_timestamp", time.time()
                                    )
                                    if entry_price > 0 and avg_fill > 0:
                                        if (
                                            getattr(
                                                self.position, "position_type", "buy"
                                            )
                                            == "sell"
                                        ):
                                            gross_profit = (
                                                entry_price - avg_fill
                                            ) * pos_contracts
                                            profit_pct = (
                                                entry_price - avg_fill
                                            ) / entry_price
                                        else:
                                            gross_profit = (
                                                avg_fill - entry_price
                                            ) * pos_contracts
                                            profit_pct = (
                                                avg_fill - entry_price
                                            ) / entry_price
                                    else:
                                        gross_profit = 0
                                        profit_pct = 0
                                    outcome = "WIN" if gross_profit > 0 else "LOSS"
                                    self.trade_logger.log_trade(
                                        {
                                            "trade_id": f"{self.asset}_{int(time.time())}",
                                            "datetime": datetime.utcnow().isoformat(),
                                            "asset": self.asset,
                                            "window_start": getattr(
                                                self, "_entry_time_iso", ""
                                            ),
                                            "window_end": "",
                                            "side": getattr(
                                                self.position, "side", "yes"
                                            ),
                                            "entry_price": round(entry_price, 4),
                                            "exit_price": round(avg_fill, 4),
                                            "contracts": pos_contracts,
                                            "profit_usd": round(gross_profit, 4),
                                            "profit_pct": round(profit_pct, 4),
                                            "outcome": outcome,
                                            "entry_time": getattr(
                                                self, "_entry_timestamp", 0
                                            ),
                                            "exit_time": int(time.time()),
                                            "held_seconds": round(held_seconds, 2),
                                            "exit_reason": exit_reason,
                                            "multiplier": getattr(
                                                self, "_current_multiplier", 1.0
                                            ),
                                            "strike_distance_pct": 0,
                                            "recent_move_pct": 0,
                                            "time_remaining_sec": time_remaining or 0,
                                            "futures_trend": getattr(
                                                self.futures,
                                                "get_trend_direction",
                                                lambda: 0,
                                            )(),
                                            "spot_price": getattr(
                                                self.futures, "get_spot", lambda: 0
                                            )(),
                                            "strike_price": getattr(self, "strike", 0),
                                            "entry_fee_usd": round(
                                                float(
                                                    getattr(self, "_last_entry_fee", 0)
                                                ),
                                                4,
                                            ),
                                            "exit_fee_usd": round(fee, 4),
                                            "net_profit_usd": round(
                                                gross_profit
                                                - float(
                                                    getattr(self, "_last_entry_fee", 0)
                                                )
                                                - fee,
                                                4,
                                            ),
                                        }
                                    )
                            else:
                                print(
                                    f"[{self.asset}] CRITICAL: Full exit failed — order for {self.position.contracts} contracts did not fill. Forcing IoC market close."
                                )
                                # Force one final IoC MARKET attempt to close the position
                                try:
                                    (
                                        filled,
                                        fee,
                                        avg_fill,
                                    ) = await self._maybe_place_market(
                                        ticker=self.current_ticker,
                                        side="yes",
                                        contracts=self.position.contracts,
                                        price=aggressive_price,
                                        action=close_action,
                                    )
                                    if filled > 0:
                                        self.position.contracts -= filled
                                        print(
                                            f"[{self.asset}] IoC emergency close: {filled} contracts at ${avg_fill:.2f}"
                                        )
                                    else:
                                        print(
                                            f"[{self.asset}] CRITICAL: IoC emergency close also failed — position may be stranded. Manual intervention required."
                                        )
                                except Exception as e:
                                    print(
                                        f"[{self.asset}] CRITICAL: IoC emergency close raised exception {e} — position may be stranded. Manual intervention required."
                                    )

                                # Always clean up regardless of outcome
                                self._exit_in_flight = False

                                # Clean up all resting orders
                                for side in ["yes", "no"]:
                                    for oid in [
                                        getattr(self, "resting_order_ids", {}).get(
                                            side
                                        ),
                                        getattr(self, "resting_sell_orders", {}).get(
                                            side
                                        ),
                                    ]:
                                        if oid:
                                            try:
                                                await self._maybe_cancel(oid)
                                            except Exception:
                                                pass
                                self.resting_order_ids = {"yes": None, "no": None}
                                self.resting_order_prices = {"yes": 0.0, "no": 0.0}
                                self.resting_order_contracts = {"yes": 0, "no": 0}
                                self.resting_sell_orders = {"yes": None, "no": None}
                                self.take_profit_placed = False
                                return

                        # 3. Clean up
                        self.take_profit_placed = False
                        # Cancel any open resting orders on successful full exit
                        for side in ["yes", "no"]:
                            oid = getattr(self, "resting_order_ids", {}).get(side)
                            if oid:
                                try:
                                    await self._maybe_cancel(oid)
                                except Exception:
                                    pass
                        old_exposure = self.risk.get_asset_exposure(self.asset)
                        if old_exposure > 0:
                            print(
                                f"[{self.asset}] Clearing exposure of ${old_exposure:.2f} for early exit"
                            )
                            self.risk.set_asset_exposure(self.asset, 0.0)
                        self.position.close()
                        self.state.exit()
                        self._exit_in_flight = False
                        self.resting_order_ids = {"yes": None, "no": None}
                        self.resting_order_prices = {"yes": 0.0, "no": 0.0}
                        self.resting_order_contracts = {"yes": 0, "no": 0}
                        self.resting_sell_orders = {"yes": None, "no": None}
                        from config import POST_TRADE_COOLDOWN_SECONDS

                        self._post_trade_cooldown_until = (
                            time.time() + POST_TRADE_COOLDOWN_SECONDS
                        )
                        print(
                            f"[{self.asset}] Post-trade cooldown activated for {POST_TRADE_COOLDOWN_SECONDS}s"
                        )
                        return
            # ----------------------------------

            # 60s hold timer: skip take-profit placement until hold period expires
            elapsed = (
                time.time() - self._entry_timestamp if self._entry_timestamp else 0
            )
            if elapsed < 60:
                return

            # We acquired a position! Check if we have placed a sell limit order yet.
            if not getattr(self, "take_profit_placed", False):
                self.take_profit_placed = True

                # First, cancel any unused resting entry orders (e.g., the other side)
                for side, oid in getattr(self, "resting_order_ids", {}).items():
                    if oid:
                        try:
                            await self._maybe_cancel(oid)
                        except Exception as e:
                            pass
                self.resting_order_ids = {"yes": None, "no": None}

                # Evaluate market conditions to set optimal take-profit relative to entry
                regime = getattr(self.signal, "current_regime", "RANGE")
                entry_price = (
                    self.position.entry_price
                    if getattr(self.position, "entry_price", None) is not None
                    else 0.40
                )
                pt = getattr(self.position, "position_type", "buy")
                close_action = "buy" if pt == "sell" else "sell"

                if pt == "sell":
                    # Short (sold YES): profit when price drops — buy back lower
                    if regime == "HIGH_VOL":
                        tp_price = max(0.01, entry_price - 0.40)
                    elif regime == "TREND":
                        tp_price = max(0.01, entry_price - 0.35)
                    else:
                        tp_price = max(0.01, entry_price - 0.30)
                else:
                    # Long (bought YES): profit when price rises — sell higher
                    if regime == "HIGH_VOL":
                        tp_price = min(0.99, entry_price + 0.40)
                    elif regime == "TREND":
                        tp_price = min(0.99, entry_price + 0.35)
                    else:
                        tp_price = min(0.99, entry_price + 0.30)

                # Snap to Kalshi's valid price grid to avoid 'invalid_price' rejections
                tp_price = self._snap_price(tp_price)

                entry_type = "Stink Bid" if entry_price <= 0.20 else "Sniper"
                direction_str = "short" if pt == "sell" else "long"
                print(
                    f"\033[92m[{self.asset}] ✅ {direction_str} {entry_type} filled! Market is {regime}. Placing take-profit {close_action} order at ${tp_price:.2f}\033[0m"
                )
                try:
                    tp_client_id = f"tp_{self.asset}_{int(time.time())}"
                    resp_tp = await self._maybe_place_limit(
                        ticker=self.current_ticker,
                        side="yes",
                        contracts=self.position.contracts,
                        price=tp_price,
                        action=close_action,
                        client_order_id=tp_client_id,
                    )
                    if isinstance(resp_tp, dict) and "order" in resp_tp:
                        if not hasattr(self, "resting_sell_orders"):
                            self.resting_sell_orders = {"yes": None, "no": None}
                        self.resting_sell_orders["yes"] = resp_tp["order"].get(
                            "order_id"
                        )
                except Exception as e:
                    print(f"[{self.asset}] Failed to place take-profit order: {e}")
                    self.take_profit_placed = True
