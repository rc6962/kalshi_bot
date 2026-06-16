import aiohttp
import websockets
import json
import time
import base64
import asyncio
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiClient:
    def __init__(self, api_key, private_key_path, base_url):
        self.api_key = api_key
        self.private_key_path = private_key_path
        # Strip trailing /trade-api/v2 from base_url to avoid duplication
        self.base_url = base_url.replace("/trade-api/v2", "").rstrip("/")
        if "demo-api.kalshi.co" in base_url:
            self.ws_url = "wss://demo-api.kalshi.co/trade-api/ws/v2"
        else:
            self.ws_url = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
        # Increased timeouts to prevent socket read timeouts on positions API
        self.request_timeout = aiohttp.ClientTimeout(total=10, connect=3, sock_read=5)
        self.private_key = self._load_key()
        self._session = None  # Persistent session for connection reuse

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
                salt_length=padding.PSS.MAX_LENGTH,
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
        """Get or create persistent aiohttp session with connection pooling."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
                force_close=False,  # Keep connections alive
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.request_timeout,
            )
        return self._session

    async def place_market_order(self, ticker, side, contracts, price=None, action="buy"):
        path = "/trade-api/v2/portfolio/orders"
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(timestamp, "POST", path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        payload = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": contracts,
            "type": "market",
            "time_in_force": "immediate_or_cancel",
        }

        if price is not None:
            price_cents = max(1, int(round(price * 100)))
            if side == "yes":
                payload["yes_price"] = price_cents
            else:
                payload["no_price"] = price_cents

        print("Submitting Kalshi order:", payload)

        try:
            session = await self._get_session()
            async with session.post(
                self.base_url + path,
                headers=headers,
                json=payload
            ) as resp:
                text = await resp.text()
                response_data = None
                if resp.headers.get("Content-Type", "").startswith("application/json"):
                    response_data = json.loads(text)

                if 200 <= resp.status < 300:
                    print(f"[KalshiClient] Order OK {resp.status}: {text[:300]}")
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
            print(f"[KalshiClient] Order TIMEOUT after {self.request_timeout.total}s: {payload}")
            raise RuntimeError(
                f"Kalshi order timed out after {self.request_timeout.total}s"
                f"\nrequest_url={self.base_url + path}"
                f"\npayload={payload}"
            ) from exc

    async def authenticated_request(self, method, path):
        # Normalize path to ensure it starts with /trade-api/v2
        if not path.startswith("/trade-api/v2"):
            clean_path = "/" + path.lstrip("/")
            path = "/trade-api/v2" + clean_path

        max_retries = 3
        backoff = 1.0

        for attempt in range(max_retries):
            timestamp = str(int(time.time() * 1000))
            signature = self._sign(timestamp, method, path)

            headers = {
                "KALSHI-ACCESS-KEY": self.api_key,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": signature,
            }

            try:
                session = await self._get_session()
                async with session.request(
                    method,
                    self.base_url + path,
                    headers=headers
                ) as resp:
                    text = await resp.text()
                    
                    if resp.status == 429:
                        if attempt < max_retries - 1:
                            wait_time = backoff * (2 ** attempt)
                            print(f"[KalshiClient] Rate limit (429) hit on {path}. Retrying in {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
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
                if attempt < max_retries - 1 and ("429" in str(e) or "too_many_requests" in str(e) or "Timeout" in str(e)):
                    wait_time = backoff * (2 ** attempt)
                    print(f"[KalshiClient] Request exception ({e}). Retrying in {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                raise

    async def get_open_positions(self):
        """Fetch open portfolio positions from Kalshi."""
        try:
            # Drop query parameters completely; they cause signature failures on Kalshi v2
            response = await self.authenticated_request("GET", "/trade-api/v2/portfolio/positions")
            
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
                        position_fp = p.get("position_fp") or p.get("total_cost_shares_fp")
                        if position_fp is not None:
                            try:
                                p["side"] = "yes" if float(position_fp) > 0 else "no"
                            except Exception:
                                p["side"] = "yes"  # fallback
                        else:
                            p["side"] = "yes"
                            
                    # Calculate entry price if missing
                    if p.get("entry_price") is None:
                        traded_dollars = p.get("total_traded_dollars") or p.get("market_exposure_dollars") or p.get("total_cost_dollars")
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
            
        return []

    async def get_balance(self):
        try:
            data = await self.authenticated_request("GET", "/trade-api/v2/portfolio/balance")
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
                print(f"[KalshiClient] Balance: raw={balance_raw} -> ${balance_dollars:.2f}")
                return balance_dollars
            else:
                print(f"[KalshiClient] No balance field in response: {list(data.keys()) if isinstance(data, dict) else data}")
            return None
        except Exception as exc:
            print(f"[KalshiClient] get_balance error: {exc}")
            return None

    async def websocket_listen(self, tickers, callback):
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
            ping_interval=20,  # Keep connection alive
            ping_timeout=10,
            close_timeout=5,
        ) as ws:

            print("WebSocket connected.")

            params = {
                "channels": ["ticker"],
                "send_initial_snapshot": True
            }
            if len(tickers) == 1:
                params["market_ticker"] = tickers[0]
            else:
                params["market_tickers"] = tickers

            subscribe_message = {
                "id": int(time.time()),
                "cmd": "subscribe",
                "params": params
            }

            payload = json.dumps(subscribe_message)
            print("WebSocket subscribe payload:", payload)
            await ws.send(payload)
            print("Subscribed to:", tickers)

            async for message in ws:
                start = time.perf_counter()
                try:
                    data = json.loads(message)
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
        print(f"[KalshiClient] Closing {count} {close_side} contracts on {ticker} (action=sell, price_floor={floor_price})")
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
