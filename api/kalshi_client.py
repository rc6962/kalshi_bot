import asyncio
import base64
import json
import time
from datetime import datetime

import aiohttp
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiClient:
    def __init__(self, api_key, private_key_path, base_url):
        self.api_key = api_key
        self.private_key_path = private_key_path
        # Strip trailing /trade-api/v2 from base_url to avoid duplication
        self.base_url = base_url.replace("/trade-api/v2", "").rstrip("/")
        if "demo.kalshi.co" in base_url:
            self.ws_url = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
        else:
            self.ws_url = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
        # REST timeouts: demo environment needs more headroom under API load
        self.request_timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        self.private_key = self._load_key()
        self._session = None  # Persistent session for connection reuse
        self.cfb_callback = None  # Callback for CF Benchmarks valuations
        # Shared sync lock: only one asset syncs positions at a time across all EventLoops
        self._position_sync_in_flight = False
        self._position_sync_lock = asyncio.Lock()
        # Circuit breaker for connection errors
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0  # Timestamp when circuit closes (0 = closed)
        self.CIRCUIT_BREAKER_THRESHOLD = 3  # Open circuit after 3 consecutive failures
        self.CIRCUIT_BREAKER_DURATION = 60.0  # Stay open for 60 seconds

        print("REST BASE:", self.base_url)
        print("WS URL:", self.ws_url)

    def _load_key(self):
        with open(self.private_key_path, "rb") as f:
            return serialization.load_pem_private_key(
                f.read(),
                password=None,
            )

    def _sign(self, timestamp, method, path):
        message = f"{timestamp}{method}{path}".encode()

        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode()

    def _get_path_with_query(self, path, params):
        if not params:
            return path
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{path}?{query_string}"

    async def _get_session(self):
        """Get or create aiohttp session. force_close=True prevents stale pooled
        connections from hanging after network blips."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=60,
                enable_cleanup_closed=True,
                force_close=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.request_timeout,
            )
        return self._session

    async def _reset_session(self):
        """Force-close and discard the current session so the next request gets a fresh one."""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    async def place_market_order(
        self, ticker, side, contracts, price=None, action="buy", reduce_only=False
    ):
        path = "/trade-api/v2/portfolio/events/orders"
        timestamp = str(int(time.time() * 1000 - 500))
        signature = self._sign(timestamp, "POST", path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        action = action.lower()
        side = side.lower()

        # Map (action, side) to V2 book_side per Kalshi official docs:
        #   (buy, yes)  → bid  (buy YES)
        #   (sell, yes) → ask  (sell YES)
        #   (buy, no)   → ask  (buy NO = sell YES at 1-price)
        #   (sell, no)  → bid  (sell NO = buy YES at 1-price)
        if action == "buy" and side == "yes":
            v2_side = "bid"
            v2_price = price if price is not None else 0.99
        elif action == "buy" and side == "no":
            v2_side = "ask"
            v2_price = (1.0 - price) if price is not None else 0.99
        elif action == "sell" and side == "yes":
            v2_side = "ask"
            v2_price = price if price is not None else 0.01
        elif action == "sell" and side == "no":
            v2_side = "bid"
            v2_price = (1.0 - price) if price is not None else 0.99
        else:
            v2_side = "bid"
            v2_price = price if price is not None else 0.99

        payload = {
            "ticker": ticker,
            "side": v2_side,
            "count": f"{contracts:.2f}",
            "price": f"{max(0.01, v2_price):.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        if reduce_only:
            payload["reduce_only"] = True

        print("Submitting Kalshi order:", payload)

        try:
            session = await self._get_session()
            async with session.post(
                self.base_url + path, headers=headers, json=payload
            ) as resp:
                text = await resp.text()
                response_data = None
                if resp.headers.get("Content-Type", "").startswith("application/json"):
                    response_data = json.loads(text)

                if 200 <= resp.status < 300:
                    print(f"[KalshiClient] Order OK {resp.status}: {text[:300]}")
                    if isinstance(response_data, dict):
                        if "order_id" not in response_data and "id" in response_data:
                            response_data["order_id"] = response_data["id"]
                        return {"order": response_data}
                    return response_data if response_data is not None else text

                print(f"[KalshiClient] Order REJECTED {resp.status}: {text[:500]}")
                print(f"[KalshiClient] Request URL: {self.base_url + path}")
                print(f"[KalshiClient] Payload: {payload}")
                raise RuntimeError(
                    f"Kalshi order failed {resp.status} {resp.reason}: {text}"
                    f"\nrequest_url={self.base_url + path}"
                    f"\npayload={payload}"
                )
        except TimeoutError as exc:
            print(
                f"[KalshiClient] Order TIMEOUT after {self.request_timeout.total}s: {payload}"
            )
            await self._reset_session()  # flush stale connections
            raise RuntimeError(
                f"Kalshi order timed out after {self.request_timeout.total}s"
                f"\nrequest_url={self.base_url + path}"
                f"\npayload={payload}"
            ) from exc

    async def place_limit_order(
        self,
        ticker,
        side,
        contracts,
        price,
        action="buy",
        client_order_id=None,
        reduce_only=False,
    ):
        path = "/trade-api/v2/portfolio/events/orders"
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(timestamp, "POST", path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        action = action.lower()
        side = side.lower()

        # Map (action, side) to V2 book_side per Kalshi official docs.
        if action == "buy" and side == "yes":
            v2_side = "bid"
            v2_price = price
        elif action == "buy" and side == "no":
            v2_side = "ask"
            v2_price = 1.0 - price
        elif action == "sell" and side == "yes":
            v2_side = "ask"
            v2_price = price
        elif action == "sell" and side == "no":
            v2_side = "bid"
            v2_price = 1.0 - price
        else:
            v2_side = "bid"
            v2_price = price

        payload = {
            "ticker": ticker,
            "side": v2_side,
            "count": f"{contracts:.2f}",
            "price": f"{max(0.01, v2_price):.4f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
        }

        if client_order_id:
            payload["client_order_id"] = client_order_id
        if reduce_only:
            payload["reduce_only"] = True

        print(f"Submitting Kalshi limit order: {payload}")

        max_retries = 3
        backoff = 1.0

        for attempt in range(max_retries):
            timestamp = str(int(time.time() * 1000))
            signature = self._sign(timestamp, "POST", path)

            headers = {
                "KALSHI-ACCESS-KEY": self.api_key,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": signature,
                "Content-Type": "application/json",
            }

            try:
                session = await self._get_session()
                async with session.post(
                    self.base_url + path, headers=headers, json=payload
                ) as resp:
                    text = await resp.text()
                    response_data = None
                    if resp.headers.get("Content-Type", "").startswith(
                        "application/json"
                    ):
                        response_data = json.loads(text)

                    if resp.status == 429:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2**attempt)
                            print(
                                f"[KalshiClient] Rate limit (429) placing limit order. Retrying in {wait_time:.1f}s..."
                            )
                            await asyncio.sleep(wait_time)
                            continue

                    if 200 <= resp.status < 300:
                        print(
                            f"[KalshiClient] Limit Order OK {resp.status}: {text[:300]}"
                        )
                        if isinstance(response_data, dict):
                            if (
                                "order_id" not in response_data
                                and "id" in response_data
                            ):
                                response_data["order_id"] = response_data["id"]
                            return {"order": response_data}
                        return response_data if response_data is not None else text

                    print(
                        f"[KalshiClient] Limit Order REJECTED {resp.status}: {text[:500]}"
                    )
                    raise RuntimeError(
                        f"Kalshi limit order failed {resp.status} {resp.reason}: {text}"
                    )
            except TimeoutError as exc:
                await self._reset_session()  # flush stale connections
                raise RuntimeError(f"Kalshi limit order timed out") from exc
            except Exception as e:
                if attempt < max_retries - 1 and (
                    "429" in str(e) or "too_many_requests" in str(e)
                ):
                    wait_time = backoff * (2**attempt)
                    print(
                        f"[KalshiClient] Exception {e} placing limit order. Retrying in {wait_time:.1f}s..."
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise

    async def cancel_order(self, order_id):
        path = f"/trade-api/v2/portfolio/orders/{order_id}/cancel"
        print(f"Canceling Kalshi order: {order_id}")
        return await self.authenticated_request("POST", path)

    async def get_portfolio_orders(self, ticker=None, status="resting"):
        path = "/trade-api/v2/portfolio/orders"
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status

        path = self._get_path_with_query(path, params)
        return await self.authenticated_request("GET", path)

    async def authenticated_request(self, method, path):
        # Normalize path to ensure it starts with /trade-api/v2
        if not path.startswith("/trade-api/v2"):
            clean_path = "/" + path.lstrip("/")
            path = "/trade-api/v2" + clean_path

        # --- CIRCUIT BREAKER ---
        if time.time() < self._circuit_open_until:
            remaining = int(self._circuit_open_until - time.time())
            raise RuntimeError(
                f"Circuit breaker OPEN — Kalshi API unavailable for ~{remaining}s. "
                f"Skipping {method} {path}."
            )

        max_retries = 3
        backoff = 1.0

        for attempt in range(max_retries):
            # Subtract 500ms buffer so timestamp is fresh when it reaches Kalshi servers
            # (network latency can push a fresh timestamp past their expiry window)
            timestamp = str(int(time.time() * 1000 - 500))
            signature = self._sign(timestamp, method, path)

            headers = {
                "KALSHI-ACCESS-KEY": self.api_key,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": signature,
            }

            try:
                session = await self._get_session()
                async with session.request(
                    method, self.base_url + path, headers=headers
                ) as resp:
                    text = await resp.text()

                    # Success: reset circuit breaker
                    self._consecutive_failures = 0

                    if resp.status == 429:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2**attempt)
                            print(
                                f"[KalshiClient] Rate limit (429) hit on {path}. Retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                            )
                            await asyncio.sleep(wait_time)
                            continue

                    if resp.status == 502 or resp.status == 503 or resp.status == 504:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2**attempt)
                            print(
                                f"[KalshiClient] Server error ({resp.status}) on {path}. Retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                            )
                            await asyncio.sleep(wait_time)
                            continue

                    # Retry 401 Unauthorized (timestamp expired) with a fresher timestamp
                    if resp.status == 401:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2**attempt)
                            print(
                                f"[KalshiClient] Auth error (401) on {path}. Retrying with fresh timestamp in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                            )
                            await asyncio.sleep(wait_time)
                            continue

                    if resp.status < 200 or resp.status >= 300:
                        raise RuntimeError(
                            f"Kalshi request failed {resp.status} {resp.reason}: {text}"
                            f"\nrequest_url={self.base_url + path}"
                        )
                    try:
                        return json.loads(text)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Failed to decode JSON from response: {text}"
                        ) from exc
            except Exception as e:
                # If exception is related to rate limiting or connection drop under load, retry
                if attempt < max_retries - 1 and (
                    "429" in str(e)
                    or "too_many_requests" in str(e)
                    or "Timeout" in str(e)
                    or "Connection timeout" in str(e)
                    or "Connection reset" in str(e)
                    or "Cannot connect" in str(e)
                ):
                    # Reset session on connection errors to flush stale sockets
                    if "Timeout" in str(e) or "Connection" in str(e):
                        await self._reset_session()
                    wait_time = backoff * (2**attempt)
                    print(
                        f"[KalshiClient] Request exception ({e}). Retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(wait_time)
                    continue

                # All retries exhausted: trip circuit breaker
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
                    self._circuit_open_until = (
                        time.time() + self.CIRCUIT_BREAKER_DURATION
                    )
                    print(
                        f"[KalshiClient] CIRCUIT BREAKER OPEN — {self._consecutive_failures} consecutive failures. "
                        f"Blocking API calls for {self.CIRCUIT_BREAKER_DURATION:.0f}s. Reset at {datetime.fromtimestamp(self._circuit_open_until).isoformat()}"
                    )
                raise

    async def get_open_positions(self):
        """Fetch open portfolio positions from Kalshi."""
        try:
            # Shared lock: only one EventLoop syncs positions at a time.
            # Other assets skip this cycle and reuse stale data (safe since
            # portfolio positions change slowly and position syncs are for
            # tracking, not for generating signals).
            if self._position_sync_lock.locked():
                return None  # Another asset is syncing — skip this cycle

            async with self._position_sync_lock:
                # Drop query parameters completely; they cause signature failures on Kalshi v2
                response = await self.authenticated_request(
                    "GET", "/trade-api/v2/portfolio/positions"
                )

            positions = []
            if isinstance(response, dict):
                # Filter for market_positions and exclude duplicate event-level positions
                if "market_positions" in response and response["market_positions"]:
                    positions.extend(response["market_positions"])
                elif "positions" in response and response["positions"]:
                    positions.extend(response["positions"])
                elif "data" in response and response["data"]:
                    positions.extend(response["data"])
            elif isinstance(response, list):
                positions.extend(response)

            # Filter for actual active positions
            active_positions = []
            for p in positions:
                raw_count = (
                    p.get("position_fp")
                    or p.get("position")
                    or p.get("count")
                    or p.get("contracts")
                    or p.get("total_cost_shares_fp")
                    or 0
                )
                try:
                    count = abs(int(float(raw_count)))
                except Exception:
                    count = 0
                if count > 0:
                    # Inject standardized count and side for ease of use across the codebase
                    p["count"] = count
                    p["position"] = count
                    p["contracts"] = count

                    # Determine side
                    if "side" not in p:
                        position_fp = p.get("position_fp") or p.get(
                            "total_cost_shares_fp"
                        )
                        if position_fp is not None:
                            try:
                                p["side"] = "yes" if float(position_fp) > 0 else "no"
                            except Exception:
                                p["side"] = "yes"  # fallback
                        else:
                            p["side"] = "yes"

                    # Calculate entry price if missing
                    if p.get("entry_price") is None:
                        traded_dollars = (
                            p.get("total_traded_dollars")
                            or p.get("market_exposure_dollars")
                            or p.get("total_cost_dollars")
                        )
                        if traded_dollars is not None:
                            try:
                                p["entry_price"] = float(traded_dollars) / count
                            except ZeroDivisionError:
                                p["entry_price"] = 0.0
                        else:
                            p["entry_price"] = 0.0

                    active_positions.append(p)
            return active_positions

        except Exception as exc:
            print(f"[KalshiClient] get_open_positions error: {exc}")

        return None  # None signals error; empty list means genuinely no positions

    async def get_balance(self):
        try:
            data = await self.authenticated_request(
                "GET", "/trade-api/v2/portfolio/balance"
            )
            balance = None
            for key in ["balance", "portfolio_balance", "total_value"]:
                if isinstance(data, dict) and key in data and data[key] is not None:
                    balance = data[key]
                    break

            if balance is not None:
                balance_raw = float(balance)
                # Kalshi returns balance in cents, convert to dollars
                # Sanity check: if raw value < 100 and seems like dollars already, use directly
                if balance_raw < 100 and (balance_raw * 100) < 1:
                    balance_dollars = balance_raw
                else:
                    balance_dollars = balance_raw / 100.0
                print(
                    f"[KalshiClient] Balance: raw={balance_raw} -> ${balance_dollars:.2f}"
                )
                return balance_dollars
            else:
                print(
                    f"[KalshiClient] No balance field in response: {list(data.keys()) if isinstance(data, dict) else data}"
                )
            return None
        except Exception as exc:
            print(f"[KalshiClient] get_balance error: {exc}")
            return None

    async def websocket_listen(self, tickers, callback, index_ids=None):
        path = "/trade-api/ws/v2"
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(timestamp, "GET", path)

        headers = [
            ("KALSHI-ACCESS-KEY", self.api_key),
            ("KALSHI-ACCESS-TIMESTAMP", timestamp),
            ("KALSHI-ACCESS-SIGNATURE", signature),
        ]

        async with websockets.connect(
            self.ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=30,  # Kalshi pings every 10s; give 30s before dropping
            open_timeout=30,  # Handshake timeout (default 10s causes drops under load)
            close_timeout=10,
        ) as ws:
            print("WebSocket connected.")

            params = {
                "channels": ["ticker", "orderbook_delta"],
                "send_initial_snapshot": True,
            }
            if len(tickers) == 1:
                params["market_ticker"] = tickers[0]
            else:
                params["market_tickers"] = tickers
            if index_ids:
                params.setdefault("channels", []).append("cfbenchmarks_value")
                params["index_ids"] = index_ids

            subscribe_message = {
                "id": int(time.time()),
                "cmd": "subscribe",
                "params": params,
            }

            payload = json.dumps(subscribe_message)
            print("WebSocket subscribe payload:", payload)
            await ws.send(payload)
            print("Subscribed to:", tickers)

            async for message in ws:
                start = time.perf_counter()
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    if (
                        msg_type == "cfbenchmarks_value"
                        and self.cfb_callback is not None
                    ):
                        await self.cfb_callback(data)
                    else:
                        await callback(data)
                except Exception as exc:
                    print(f"WebSocket callback error: {exc}")
                finally:
                    latency_ms = (time.perf_counter() - start) * 1000

    async def close_position(self, ticker, exit_price=None):
        """Close an open position by placing an opposite-side sell order.

        Fetches the current position for the given ticker and sells to close it.
        We always set the floor price to $0.01 (1 cent) to guarantee immediate execution.
        Returns the close order response or None if no position to close.
        """
        try:
            positions = await self.get_open_positions()
        except Exception as exc:
            print(f"[KalshiClient] Failed to fetch positions for close: {exc}")
            return None

        if positions is None:
            print(
                f"[KalshiClient] Positions fetch failed for close — API may be unavailable"
            )
            return None

        # Find the position matching this ticker
        target_position = None
        for pos in positions:
            pos_ticker = pos.get("ticker") or pos.get("market_ticker") or ""
            if pos_ticker == ticker:
                target_position = pos
                break

        if target_position is None:
            print(f"[KalshiClient] No open position found for {ticker} to close")
            return None

        # Determine what we hold and sell the same side
        side_held = (target_position.get("side") or "").lower()
        raw_count = (
            target_position.get("position")
            or target_position.get("count")
            or target_position.get("contracts")
            or 0
        )
        count = abs(int(float(raw_count)))

        if side_held == "yes":
            close_side = "yes"
        elif side_held == "no":
            close_side = "no"
        else:
            print(f"[KalshiClient] Unknown position side for {ticker}: {side_held}")
            return None

        if count <= 0:
            print(f"[KalshiClient] Position count is 0 for {ticker}, nothing to close")
            return None

        # Always use $0.01 floor to guarantee the order matches immediately with the best bid
        floor_price = 0.01
        print(
            f"[KalshiClient] Closing {count} {close_side} contracts on {ticker} (action=sell, price_floor={floor_price})"
        )
        return await self.place_market_order(
            ticker=ticker,
            side=close_side,
            contracts=count,
            action="sell",
            price=floor_price,
        )

    async def close(self):
        """Close persistent connections."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
