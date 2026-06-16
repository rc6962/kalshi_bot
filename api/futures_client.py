import websockets
import asyncio
import json
from collections import deque
import time
import math
from config import VOLATILITY_WINDOW_SECONDS, PRICE_SAMPLE_INTERVAL


class FuturesClient:
    def __init__(self, symbol):
        self.symbol = symbol.lower()
        # Max capacity for 20 minutes at 1 sample/sec is 1200
        self.prices = deque(maxlen=1200)
        self.last_price = None
        self.last_sample_time = 0
        self.sample_interval = PRICE_SAMPLE_INTERVAL
        self.ws_task = None
        self._stop_event = asyncio.Event()

    async def connect(self):
        # Use the bookTicker endpoint which we confirmed is working
        urls = [
            f"wss://fstream.binance.com/ws/{self.symbol}@bookTicker",  # Book ticker - confirmed working
            f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker",  # Alternative book ticker
            f"wss://fstream.binance.com/ws/{self.symbol}@ticker",  # Fallback to ticker
        ]
        print(f"Connecting to Binance WebSocket for {self.symbol.upper()}")
        
        for idx, url in enumerate(urls):
            print(f"Trying WebSocket URL {idx+1}/{len(urls)}: {url}")
            try:
                # Run the WebSocket connection in the background
                self.ws_task = asyncio.create_task(self._run_websocket_connection(url))
                return  # Return immediately after starting the connection
            except Exception as e:
                print(f"Failed to start connection to Binance WebSocket: {e} (URL {idx+1}/{len(urls)})")
                if idx == len(urls) - 1:  # Last URL in the list
                    raise
                else:
                    continue  # Try the next URL

    async def _run_websocket_connection(self, url):
        """Run the WebSocket connection in the background"""
        try:
            async with websockets.connect(url) as ws:
                print(f"Connected to Binance for {self.symbol.upper()}. Waiting for data...")
                
                async for msg in ws:
                    if self._stop_event.is_set():
                        break
                        
                    data = json.loads(msg)
                    
                    # Handle bookTicker format (confirmed working in our test)
                    price = None
                    if 'b' in data and 'a' in data:  # bookTicker format - using bid price
                        price = float(data["b"])  # Using bid price as the spot price
                    elif 'c' in data:  # ticker format fallback
                        price = float(data["c"])
                    elif 'p' in data:  # miniTicker format fallback
                        price = float(data["p"])
                        
                    if price and price > 0:
                        self.last_price = price
                        #print(f"[DEBUG] Price updated: ${price:,.2f}")  # Comment out to reduce spam
                        
                        now = time.time()
                        is_first = len(self.prices) == 0
                        if now - self.last_sample_time >= self.sample_interval:
                            self.prices.append((now, price))
                            self.last_sample_time = now
                        
                        if is_first and len(self.prices) == 1:
                            print(f"First price received: ${price:,.2f}")
                    elif "code" in data and "msg" in data:  # error from binance
                        print(f"Binance error for {self.symbol}: {data['msg']}")
                        raise ValueError(f"Binance subscription failed: {data['msg']}")
        except Exception as e:
            print(f"WebSocket connection error for {self.symbol}: {e}")
            raise

    def get_spot(self):
        """Get current spot price"""
        return self.last_price

    def get_recent_move_pct(self, window_seconds=300):
        """Calculate percentage move over the specified window_seconds."""
        if not self.prices:
            return 0.0

        now = time.time()
        cutoff = now - window_seconds
        recent = [(t, p) for t, p in self.prices if t >= cutoff]
        if len(recent) < 5:
            return 0.0

        start = recent[0][1]
        current = self.last_price or recent[-1][1]

        if not start:
            return 0.0

        return (current - start) / start

    def get_rolling_volatility(self, window_seconds=VOLATILITY_WINDOW_SECONDS):
        """Calculate standard deviation of 1-second log returns over window_seconds."""
        now = time.time()
        cutoff = now - window_seconds
        recent = [p for t, p in self.prices if t >= cutoff]
        
        # Need at least 10 price points to compute returns and std dev
        if len(recent) < 10:
            return 0.0001  # Safe default (0.01% volatility per second)

        log_returns = []
        for i in range(1, len(recent)):
            if recent[i-1] > 0 and recent[i] > 0:
                log_returns.append(math.log(recent[i] / recent[i-1]))

        if len(log_returns) < 5:
            return 0.0001

        mean = sum(log_returns) / len(log_returns)
        variance = sum((x - mean) ** 2 for x in log_returns) / len(log_returns)
        vol = math.sqrt(variance)
        
        return max(vol, 1e-6)  # Avoid returning exactly 0

    def get_trend_direction(self, window_seconds=60):
        now = time.time()
        cutoff = now - window_seconds
        recent = [(t, p) for t, p in self.prices if t >= cutoff]
        if len(recent) < 10:
            return 0

        start_price = recent[0][1]
        end_price = self.last_price or recent[-1][1]
        change_pct = (end_price - start_price) / start_price

        if change_pct > 0.0005:
            return 1
        elif change_pct < -0.0005:
            return -1
        return 0

    async def close(self):
        """Close the WebSocket connection"""
        self._stop_event.set()
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass