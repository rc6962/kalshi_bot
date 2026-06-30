"""
Coinbase Advanced Trade API V3 Client
Drop-in replacement for CoinbaseClient with Advanced Trade WebSocket support.
Uses CDP API keys with JWT authentication for WebSocket connections.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections import deque
from typing import Callable, Optional

import jwt
import websockets


class CoinbaseAdvancedClient:
    """
    Coinbase Advanced Trade API V3 WebSocket Client.

    Uses CDP API keys with JWT authentication for WebSocket connections.
    Provides compatible interface with existing CoinbaseClient.
    """

    def __init__(
        self,
        symbol: str,
        api_key: str,
        private_key: str,
        on_tick_callback: Optional[Callable] = None,
    ):
        self.symbol = symbol.upper()
        self.api_key = api_key
        self.private_key = private_key
        self.on_tick_callback = on_tick_callback

        # Price tracking (compatible with existing interface)
        self.prices = deque(maxlen=300)
        self.last_price = None
        self.ws_task = None
        self._stop_event = asyncio.Event()
        self._ws_backoff_count = 0

        # Advanced Trade WebSocket endpoint
        self.ws_url = "wss://advanced-trade-ws.coinbase.com"

        # Product ID format for Advanced Trade (e.g., "BTC-USD")
        self.product_id = self._format_product_id(symbol)

    def _format_product_id(self, symbol: str) -> str:
        """Convert symbol to Advanced Trade product ID format."""
        # Map common symbols to Advanced Trade format
        symbol_map = {
            "BTC": "BTC-USD",
            "ETH": "ETH-USD",
            "SOL": "SOL-USD",
            "DOGE": "DOGE-USD",
            "XRP": "XRP-USD",
            "HYPE": "HYPE-USD",
            "BNB": "BNB-USD",
        }
        return symbol_map.get(symbol.upper(), f"{symbol.upper()}-USD")

    def _generate_jwt(self) -> str:
        """Generate JWT token for Advanced Trade WebSocket authentication."""
        # CDP API keys are Ed25519. The private key is base64-encoded raw 64-byte
        # Ed25519 private key (seed + public key). Must use EdDSA, not ES256.
        now = int(time.time())
        payload = {
            "iss": self.api_key,
            "sub": self.api_key,
            "aud": "coinbase-advanced-trade",
            "iat": now,
            "exp": now + 120,  # 2 minute expiry
            "uri": "wss://advanced-trade-ws.coinbase.com",
        }

        # Decode base64 private key and load as Ed25519 key object
        private_key_bytes = base64.b64decode(self.private_key)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        # CDP keys are 64 bytes (32-byte seed + 32-byte public key). PyJWT/cryptography
        # accepts the raw 32-byte seed or the 64-byte combined form via Ed25519PrivateKey.
        try:
            ed_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes[:32])
        except Exception:
            # Fallback: try loading the full 64 bytes (some CDP key formats)
            ed_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)

        token = jwt.encode(payload, ed_key, algorithm="EdDSA")
        return token

    async def connect(self):
        """Establish WebSocket connection to Advanced Trade."""
        jwt_token = self._generate_jwt()

        # Advanced Trade WebSocket requires JWT in connection
        headers = {
            "Authorization": f"Bearer {jwt_token}",
        }

        self.ws_task = asyncio.create_task(self._run(headers))

    async def _run(self, headers):
        """Main WebSocket connection loop with reconnection logic."""
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    # Subscribe to ticker channel
                    subscribe = {
                        "type": "subscribe",
                        "channels": [
                            {"name": "ticker", "product_ids": [self.product_id]}
                        ],
                    }
                    await ws.send(json.dumps(subscribe))
                    print(f"[CoinbaseAdvanced] Connected to {self.product_id}")

                    self._ws_backoff_count = 0

                    async for msg in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            data = json.loads(msg)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            print(f"[CoinbaseAdvanced] Message handling error: {e}")

            except Exception as e:
                if not self._stop_event.is_set():
                    delay = min(30, 3 * (1 + self._ws_backoff_count))
                    self._ws_backoff_count += 1
                    print(
                        f"[CoinbaseAdvanced] Connection error for {self.symbol}: {e}. Reconnecting in {delay}s..."
                    )
                    await asyncio.sleep(delay)

    async def _handle_message(self, data: dict):
        """Handle incoming WebSocket messages."""
        # Advanced Trade ticker message format
        if data.get("channel") == "ticker":
            for event in data.get("events", []):
                if event.get("type") == "update":
                    price_str = event.get("price")
                    if price_str:
                        price = float(price_str)
                        self.last_price = price
                        now = time.time()
                        self.prices.append((now, price))
                        self._ws_backoff_count = 0
                        if self.on_tick_callback:
                            try:
                                asyncio.create_task(self.on_tick_callback(price))
                            except Exception:
                                pass

    def get_spot(self) -> Optional[float]:
        """Get current spot price."""
        return self.last_price

    def get_momentum_bps(self, window_seconds: int = 60) -> Optional[float]:
        """Calculate momentum in basis points over window."""
        cutoff = time.time() - window_seconds
        recent = [(t, p) for t, p in self.prices if t >= cutoff]
        if len(recent) < 5:
            return None
        start_price = recent[0][1]
        end_price = recent[-1][1]
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price * 10000

    async def close(self):
        """Close WebSocket connection."""
        self._stop_event.set()
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass


# Factory function to create appropriate client based on config
def create_coinbase_client(
    symbol: str, config: dict, on_tick_callback: Optional[Callable] = None
) -> "CoinbaseClient":
    """
    Factory to create appropriate Coinbase client based on configuration.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')
        config: Configuration dict with API credentials
        on_tick_callback: Callback for price updates

    Returns:
        CoinbaseClient or CoinbaseAdvancedClient instance
    """
    use_advanced = config.get("USE_COINBASE_ADVANCED", False)

    if use_advanced:
        # Use Advanced Trade API V3
        api_key = config.get("COINBASE_API_KEY")
        private_key = config.get("COINBASE_PRIVATE_KEY")
        if not api_key or not private_key:
            raise ValueError(
                "Advanced Trade requires COINBASE_API_KEY and COINBASE_PRIVATE_KEY"
            )
        return CoinbaseAdvancedClient(
            symbol=symbol,
            api_key=api_key,
            private_key=private_key,
            on_tick_callback=on_tick_callback,
        )
    else:
        # Use legacy public feed
        from api.futures_coinbase import CoinbaseClient

        return CoinbaseClient(symbol=symbol, on_tick_callback=on_tick_callback)
