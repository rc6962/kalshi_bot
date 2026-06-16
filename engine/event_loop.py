import asyncio
import calendar
import re
import time
from datetime import datetime
from config import NO_ENTRY_LAST_SECONDS, PROFIT_PROTECTION_TRIGGER
from engine.signal_engine import SignalEngine
from engine.position_manager import PositionManager
from engine.state_machine import StateMachine
from engine.risk_manager import RiskManager
from engine.latency_optimizer import MicroOptimizations


class RegexPatterns:
    PRICE = re.compile(r"\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)")
    TARGET = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)(?=\s*target)", re.IGNORECASE)
    TARGET_PREFIX = re.compile(r"target[^\d]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)", re.IGNORECASE)
    BOUNDS = re.compile(r"\b([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?)\b")

class EventLoop:
    def __init__(
        self,
        kalshi_client,
        futures_client,
        asset="BTC",
        reserve_position=None,
        release_position=None,
        trade_logger=None,
        global_exposures=None,
        portfolio_positions=None,
    ):
        self.asset = str(asset).upper()
        self.kalshi = kalshi_client
        self.futures = futures_client
        self.signal = SignalEngine()
        self.position = PositionManager()
        self.state = StateMachine()
        self.risk = RiskManager(global_exposure_dict=global_exposures)
        self.reserve_position = reserve_position
        self.release_position = release_position
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

        self.series_candidates = [f"KX{self.asset}15M", f"KX{self.asset}"]

        self.trade_count = 0
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0
        self.total_profit = 0.0
        self.total_loss = 0.0
        self.trade_history = []
        self._portfolio_balance_ref = None

    async def start_futures(self):
        await self.futures.connect()

    async def initialize(self):
        await self.discover_market()
        await self.load_existing_position()

    async def load_existing_position(self):
        """Load existing position from Kalshi portfolio."""
        try:
            # Prefer reference cache to avoid network hits, fallback to API if not initialized
            if self._portfolio_positions_ref is not None and self._portfolio_positions_ref.get("value") is not None:
                positions = self._portfolio_positions_ref["value"]
            else:
                positions = await self.kalshi.get_open_positions()
            if positions and self.current_ticker:
                for pos in positions:
                    pos_ticker = (
                        pos.get("market_ticker")
                        or pos.get("ticker")
                    )
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
                            # If price is in cents (integer value > 1), convert to dollars
                            if entry_price > 1:
                                entry_price = entry_price / 100
                        except Exception:
                            entry_price = 0
                            
                        self.position.entry_price = entry_price
                        self.position.side = pos.get("side")
                        
                        # Calculate and set exposure for this position
                        if self.position.entry_price and self.position.contracts:
                            position_value = self.position.entry_price * self.position.contracts
                            self.risk.set_asset_exposure(self.asset, position_value)
                        
                        print(f"[{self.asset}] Loaded existing position: {self.position.contracts} contracts at ${self.position.entry_price:.2f}, exposure: ${self.risk.get_asset_exposure(self.asset):.2f}")
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
                print(f"[{self.asset}] Clearing exposure of ${old_exposure:.2f} for expired market")
                self.risk.set_asset_exposure(self.asset, 0.0)
            self.state.exit()
            self.position.close()
            self._debugged_first_ticker = False
            self._last_position_sync = 0

            await self.discover_market()
            await self.load_existing_position()
            changed = self.current_ticker != old_ticker
            print(f"[{self.asset}] Market rollover:", old_ticker, "->", self.current_ticker)
            return changed
        except Exception as exc:
            print(f"[{self.asset}] Market rollover failed:", exc)
            return False
        finally:
            self._market_rollover_in_flight = False

    async def sync_current_position(self):
        now = time.time()
        if now - self._last_position_sync < 5:
            return
        self._last_position_sync = now

        if self._portfolio_positions_ref is not None:
            open_positions = self._portfolio_positions_ref.get("value") or []
        else:
            open_positions = await self.kalshi.get_open_positions()
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
                or position.get("yes_price_dollars")
                or position.get("no_price_dollars")
            )
            if entry_price is not None:
                try:
                    entry_price = float(entry_price)
                    if entry_price > 1:
                        entry_price = entry_price / 100
                except Exception:
                    entry_price = None

            signal = "ENTER_YES" if side == "yes" else "ENTER_NO"
            self.state.enter(signal)
            self.position.open(entry_price, contracts, side)

            # Update exposure for the loaded position
            if entry_price and contracts:
                position_value = entry_price * contracts
                self.risk.set_asset_exposure(self.asset, position_value)
            
            print(f"[{self.asset}] Synced portfolio position:", ticker, side, contracts, f"entry_price=${entry_price:.2f}", f"exposure=${self.risk.get_asset_exposure(self.asset):.2f}")
            return

        if not self.state.can_enter():
            print(f"[{self.asset}] No portfolio position found for current ticker; resetting state.")
            # Clear exposure if no position exists
            if self.risk.get_asset_exposure(self.asset) > 0:
                print(f"[{self.asset}] Clearing exposure tracking as position was closed externally")
                self.risk.set_asset_exposure(self.asset, 0.0)
            if self.position.entry_price is not None:
                print(f"[{self.asset}] But we have an in-memory position (entry=${self.position.entry_price:.2f}). Keeping state; the order may still be filling.")
                return
            self.state.exit()
            self.position.close()

    def record_trade(self, entry_price, exit_price, contracts, side):
        pnl = self.position._pnl(exit_price)
        profit = pnl * contracts
        self.trade_count += 1
        if pnl >= 0:
            self.win_count += 1
            self.total_profit += profit
        else:
            self.loss_count += 1
            self.total_loss += profit
        self.total_pnl += pnl

        trade = {
            "asset": self.asset,
            "ticker": self.current_ticker,
            "side": side,
            "contracts": contracts,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "profit": profit,
            "timestamp": int(time.time()),
        }
        self.trade_history.append(trade)
        return pnl, profit

    def print_trade_stats(self):
        avg_pnl = self.total_pnl / self.trade_count if self.trade_count else 0.0
        print(
            f"[{self.asset}] Trades={self.trade_count}",
            f"Wins={self.win_count}",
            f"Losses={self.loss_count}",
            f"AvgPnL={avg_pnl:.4%}",
            f"TotalProfit=${self.total_profit:.4f}",
            f"TotalLoss=${self.total_loss:.4f}"
        )

    async def discover_market(self):
        print(f"Discovering {self.asset} market...")

        spot = self.futures.get_spot()
        print(f"{self.asset} spot price:", spot)

        now = int(time.time())
        window_seconds = 15 * 60 + 60
        min_entry_ts = now + NO_ENTRY_LAST_SECONDS

        def parse_event_time(value):
            if not value:
                return None

            if value.endswith("Z"):
                value = value[:-1] + "+00:00"

            try:
                parsed = time.strptime(value.replace("+00:00", "Z"), "%Y-%m-%dT%H:%M:%SZ")
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
                    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
                }
                month = month_map.get(month_str)
                if month is None:
                    return None

                expiry_struct = time.struct_time((
                    time.gmtime().tm_year, month, day,
                    hour, minute, 0, 0, 0, 0
                ))
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
                        path += f"&min_close_ts={now}&max_close_ts={now + window_seconds}"
                    else:
                        path += f"&min_close_ts={now}"

                    if cursor:
                        path += f"&cursor={cursor}"

                    print(f"Querying Kalshi: {path}")
                    try:
                        data = await asyncio.wait_for(self.kalshi.authenticated_request("GET", path), timeout=30.0)
                    except asyncio.TimeoutError:
                        print(f"Market discovery timed out for {series}, moving to next...")
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

                        expiry = parse_event_time(event.get("close_time"))
                        if expiry is None:
                            expiry = parse_event_time(event.get("strike_date"))
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
        print("Selected event:", soonest_event, "series=", selected_series, "status=", event_status, "title=", event_title)
        print("Event expiry:", selected_event_expiry, "seconds from now:", selected_event_expiry - now)

        if selected_event_expiry - now <= window_seconds:
            print(f"Selected current 15-minute {self.asset} event:", soonest_event)
        else:
            print(f"No 15-minute {self.asset} event found; falling back to nearest {self.asset} event:", soonest_event)

        markets = selected_event_markets or []
        if not markets:
            try:
                markets_data = await asyncio.wait_for(
                    self.kalshi.authenticated_request(
                        "GET",
                        f"/markets?event_ticker={soonest_event}&limit=200"
                    ), 
                    timeout=30.0
                )
                markets = markets_data.get("markets", [])
            except asyncio.TimeoutError:
                print(f"Markets lookup timed out for {soonest_event}")
                raise Exception(f"Could not fetch markets for {soonest_event}")

        event_strike = parse_event_target(selected_event)
        print("Parsed event target strike:", event_strike)
        if event_strike is None:
            print("Warning: could not parse event target from event text, falling back to market strike parsing.")

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
                    market.get("no_sub_title")
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

            if strike is None and event_strike is None and market.get("market_type") != "binary":
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
                print("  market", market.get("ticker"), "close_time", market.get("close_time"))
            raise Exception("No strike markets found for selected event.")

        self.current_ticker, self.strike, self.expiry = selected_market

        print(f"Selected {self.asset} market:")
        print("Ticker:", self.current_ticker)
        print("Strike:", self.strike)

    async def handle_ticker(self, data):
        try:
            return await self._handle_ticker_inner(data)
        except Exception as exc:
            print(f"[{self.asset}] ticker handler crashed: {exc}")
            import traceback
            traceback.print_exc()

    async def _handle_ticker_inner(self, data):
        if data.get("type") != "ticker":
            return

        ticker_data = data.get("data") or data.get("msg") or {}
        ticker = (
            ticker_data.get("market_ticker")
            or ticker_data.get("ticker")
            or ticker_data.get("event_ticker")
        )
        if ticker and ticker != self.current_ticker:
            return

        print(f"[{self.asset}] Processing ticker...")
        # NOTE: We do NOT force IDLE every tick. The state machine tracks whether
        # we're in a position. sync_current_position() handles state transitions
        # from the API. Forcing IDLE would break exit logic and allow duplicates.
        
        if self.market_expired():
            await self.sync_current_position()
            print(f"[{self.asset}] Market expired. Resetting memory position and state to IDLE for settlement.")
            self.position.close()
            self.state.exit()
            print(f"[{self.asset}] Ignoring expired market ticker:", self.current_ticker)
            return

        if not self._debugged_first_ticker:
            print(f"[{self.asset}] First ticker payload:", data)
            from config import LOG_RAW_TICKER_KEYS
            if LOG_RAW_TICKER_KEYS:
                print(f"[{self.asset}] Raw ticker_data keys: {sorted(ticker_data.keys())}")
                # Dump all key-value pairs for debugging
                for k, v in sorted(ticker_data.items()):
                    print(f"  {k} = {v}")
            self._debugged_first_ticker = True

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

        print("Ticker yes/no prices after normalization:", yes_price, no_price)

        if yes_price is None and no_price is None:
            print("Ticker missing yes/no prices:", ticker_data)
            return

        spot = self.futures.get_spot()
        if not spot:
            return

        # Initialize strike if it is None (e.g. for Up or Down markets where strike is TBD at discovery)
        if self.strike is None:
            self.strike = spot
            print(f"[{self.asset}] Strike was None/TBD; initialized to current spot price: {self.strike}")

        move_pct = self.futures.get_recent_move_pct()
        time_remaining = self.expiry - int(time.time())
        print(f"[{self.asset}] move_pct={move_pct}, time_remaining={time_remaining}, state={self.state.state}")

        # ENTRY LOGIC
        if self.state.can_enter():
            if self._order_in_flight:
                return

            # Extract bid_size and ask_size from ticker data
            bid_size = ticker_data.get("yes_bid_size") or ticker_data.get("bid_size") or 0
            ask_size = ticker_data.get("yes_ask_size") or ticker_data.get("ask_size") or 0

            # Safely calculate multiplier with error handling
            try:
                multiplier = self.risk.calculate_multiplier(spot, self.strike)
            except Exception as mult_err:
                print(f"[{self.asset}] Multiplier calculation failed: {mult_err}, using default 1.0")
                multiplier = 1.0

            # Extract bid and ask separately for the spread filter to work correctly
            yes_bid = MicroOptimizations.fast_normalize_price(
                ticker_data.get("yes_bid") or ticker_data.get("yes_bid_dollars")
            )
            yes_ask = MicroOptimizations.fast_normalize_price(
                ticker_data.get("yes_ask") or ticker_data.get("yes_ask_dollars")
            )
            # Fall back to mid-price if one side is missing
            if yes_bid is None:
                yes_bid = yes_price
            if yes_ask is None:
                yes_ask = yes_price
            
            print(f"[{self.asset}] Calling signal.evaluate with spot={spot}, strike={self.strike}, multiplier={multiplier}")
            signal = self.signal.evaluate(
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
                futures_trend=self.futures.get_trend_direction()
            )

            if signal:
                print(f"[{self.asset}] Signal: {signal}, Yes/No: {yes_price}/{no_price}, Spot: {spot}, Strike: {self.strike}")

                # Place order based on signal
                side = "yes" if signal == "ENTER_YES" else "no"
                if side == "yes":
                    base_price = yes_ask if yes_ask is not None else yes_price
                else:
                    no_ask = MicroOptimizations.fast_normalize_price(
                        ticker_data.get("no_ask") or ticker_data.get("no_ask_dollars")
                    )
                    if no_ask is None and yes_bid is not None:
                        no_ask = 1.0 - yes_bid
                    base_price = no_ask if no_ask is not None else no_price
                
                # Add 1 cent slippage buffer, capped at the $0.50 maximum allowed price
                price = min(0.50, base_price + 0.01) if base_price is not None else 0.01
                
                # Calculate number of contracts respecting the $5 limit per asset
                contracts = self.risk.calculate_contracts(
                    price=price,
                    asset_name=self.asset,
                    multiplier=multiplier,
                    recent_pnl_pct=self.risk.get_recent_performance(),
                    win_prob=0.55  # Default win probability
                )
                
                # Make sure we have at least 1 contract if the algorithm allows it
                if contracts > 0:
                    try:
                        self._order_in_flight = True
                        order_result = await self.kalshi.place_market_order(
                            ticker=self.current_ticker,
                            side=side,
                            contracts=contracts,
                            price=price  # Price in dollars, will be converted to cents internally
                        )
                        
                        # Extract actual filled contracts from order response
                        order_details = {}
                        if isinstance(order_result, dict):
                            order_details = order_result.get("order") or order_result
                        
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
                            # Fallback: if API call succeeded and we got a valid dict, assume filled
                            filled_contracts = contracts
                        
                        if filled_contracts > 0:
                            # Update asset exposure after successful order
                            exposure_increase = price * filled_contracts
                            current_exposure = self.risk.get_asset_exposure(self.asset)
                            self.risk.set_asset_exposure(self.asset, current_exposure + exposure_increase)
                            
                            # Track position and state in memory so we don't re-enter
                            self.position.open(price, filled_contracts, side)
                            self.state.enter(signal)
                            self._entry_timestamp = time.time()
                            self._entry_time_iso = datetime.utcnow().isoformat()
                            
                            # Green highlighting for successful orders
                            print(f"\033[92m[{self.asset}] ✅ ORDER EXECUTED: {order_result}\033[0m")
                            print(f"[{self.asset}] Order: {filled_contracts} contracts at ${price:.2f}, total value: ${exposure_increase:.2f}")
                            print(f"[{self.asset}] Current exposure: ${self.risk.get_asset_exposure(self.asset):.2f}")
                        else:
                            print(f"[{self.asset}] ⚠️ Order placed but filled 0 contracts (canceled/expired). Not entering position.")
                    except Exception as e:
                        print(f"[{self.asset}] Order placement failed: {e}")
                    finally:
                        self._order_in_flight = False
                else:
                    print(f"[{self.asset}] Skipping trade: No contracts available within $5 limit")

        # EXIT LOGIC
        else:
            current_price = yes_price if self.position.side == "yes" else no_price
            if current_price is not None:
                futures_trend = self.futures.get_trend_direction()
                exit_signal = self.position.update(current_price, futures_trend, time_remaining, move_pct)

                if exit_signal == "EXIT":
                    # Capture position fields NOW — before position.close() resets them to None
                    exit_side = self.position.side
                    exit_entry_price = self.position.entry_price
                    exit_contracts = self.position.contracts

                    try:
                        # Close position — use pure market order (exit_price=None) to guarantee execution
                        close_result = await self.kalshi.close_position(
                            self.current_ticker,
                            exit_price=None,
                        )
                        print(f"[{self.asset}] Position closed response: {close_result}")
                        
                        # Extract actual filled contracts from close response
                        order_details = {}
                        if isinstance(close_result, dict):
                            order_details = close_result.get("order") or close_result
                        
                        raw_fill = (
                            order_details.get("fill_count")
                            or order_details.get("fill_count_fp")
                            or order_details.get("filled_count")
                            or order_details.get("filled_contracts")
                        )
                        filled_close = 0
                        if raw_fill is not None:
                            try:
                                filled_close = int(float(raw_fill))
                            except Exception:
                                filled_close = 0
                        else:
                            # Fallback if no raw_fill is present but request succeeded
                            filled_close = exit_contracts
                            
                        if filled_close > 0:
                            # Update asset exposure after closing position
                            current_exposure = self.risk.get_asset_exposure(self.asset)
                            position_value = (exit_entry_price or 0) * filled_close
                            new_exposure = max(0, current_exposure - position_value)
                            self.risk.set_asset_exposure(self.asset, new_exposure)
                            
                            # Correct PnL direction:
                            #   YES bet profits when price rises  → profit = exit - entry
                            #   NO  bet profits when price falls  → profit = entry - exit
                            raw_profit_per_contract = current_price - (exit_entry_price or 0)
                            profit_usd = raw_profit_per_contract * filled_close
                            profit_pct = (
                                raw_profit_per_contract / exit_entry_price * 100
                                if exit_entry_price and exit_entry_price > 0 else 0.0
                            )
                            outcome = "WIN" if profit_usd >= 0 else "LOSS"

                            # Update risk manager with realized performance
                            self.risk.update_performance(
                                pnl_pct=profit_pct / 100,
                                profit_usd=profit_usd,
                            )

                            # Reset position and state AFTER logging
                            self.position.close()
                            self.state.exit()
                            
                            # Log the trade if trade_logger is available
                            if self.trade_logger:
                                trade_record = {
                                    "trade_id": f"{self.asset}_{int(time.time())}",
                                    "datetime": datetime.utcnow().isoformat(),
                                    "asset": self.asset,
                                    "window_start": getattr(self, '_entry_time_iso', ''),
                                    "window_end": datetime.utcnow().isoformat(),
                                    "side": exit_side,
                                    "entry_price": exit_entry_price,
                                    "exit_price": current_price,
                                    "contracts": filled_close,
                                    "profit_usd": profit_usd,
                                    "profit_pct": profit_pct,
                                    "outcome": outcome,
                                    "entry_time": getattr(self, '_entry_time_iso', ''),
                                    "exit_time": datetime.utcnow().isoformat(),
                                    "held_seconds": time.time() - getattr(self, '_entry_timestamp', time.time()),
                                    "multiplier": multiplier if 'multiplier' in locals() else 1.0,
                                    "strike_distance_pct": abs((spot - self.strike) / self.strike) if spot and self.strike else 0,
                                    "recent_move_pct": move_pct,
                                    "time_remaining_sec": time_remaining,
                                    "futures_trend": self.futures.get_trend_direction(),
                                    "spot_price": spot,
                                    "strike_price": self.strike
                                }
                                self.trade_logger.log_trade(trade_record)
                                print(f"[{self.asset}] Trade logged: {outcome} | profit=${profit_usd:.4f} ({profit_pct:.2f}%) | side={exit_side} | entry={exit_entry_price:.3f} exit={current_price:.3f} contracts={filled_close}")
                        else:
                            print(f"[{self.asset}] ⚠️ Close order placed but filled 0 contracts. Position remains OPEN.")
                            
                    except Exception as e:
                        print(f"[{self.asset}] Position closure failed: {e}")
