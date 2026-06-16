"""Mock futures client for testing when Binance is blocked"""
import asyncio
import random
import time
import math
from collections import deque


class MockFuturesClient:
    def __init__(self, symbol):
        self.symbol = symbol.lower()
        self.last_price = 50000.0
        self.prices = deque(maxlen=1200)
        self.last_sample_time = 0
        self.sample_interval = 1.0
        self.random = random.Random()
        self.trend = 0  # -1 downtrend, 0 sideways, 1 uptrend
        self.trend_change_counter = 0
        
    async def connect(self):
        """Simulate WebSocket connection and price feed"""
        print(f"[MOCK] Connected to {self.symbol} price feed (simulated)")
        
        while True:
            await asyncio.sleep(self.sample_interval)
            
            # Simulate realistic price movements
            # Occasionally change trend
            self.trend_change_counter += 1
            if self.trend_change_counter > 50:  # Change trend every ~50 seconds
                self.trend = random.choice([-1, 0, 1])
                self.trend_change_counter = 0
                print(f"[MOCK] Trend changed to: {['DOWN', 'SIDEWAYS', 'UP'][self.trend+1]}")
            
            # Base movement with trend
            if self.trend == 1:  # Uptrend
                change_pct = random.uniform(-0.0005, 0.0015)
            elif self.trend == -1:  # Downtrend
                change_pct = random.uniform(-0.0015, 0.0005)
            else:  # Sideways
                change_pct = random.uniform(-0.0008, 0.0008)
            
            # Add occasional volatility spikes
            if random.random() < 0.05:  # 5% chance of volatility spike
                change_pct *= random.uniform(2, 5)
                print(f"[MOCK] Volatility spike!")
            
            # Update price
            self.last_price *= (1 + change_pct)
            
            # Keep price in reasonable range ($40k-$60k)
            self.last_price = max(40000, min(60000, self.last_price))
            
            # Sample for historical data
            now = time.time()
            if now - self.last_sample_time >= self.sample_interval:
                self.prices.append((now, self.last_price))
                self.last_sample_time = now
            
            # Simulate price output occasionally
            if random.random() < 0.1:  # 10% of samples
                print(f"[MOCK] {self.symbol.upper()} price: ${self.last_price:,.2f}")
    
    def get_spot(self):
        """Get current spot price"""
        return self.last_price
    
    def get_recent_move_pct(self, window_seconds=300):
        """Calculate percentage move over window_seconds"""
        if len(self.prices) < 5:
            return 0.0
        
        now = time.time()
        cutoff = now - window_seconds
        recent = [(t, p) for t, p in self.prices if t >= cutoff]
        
        if len(recent) < 5:
            return 0.0
        
        start = recent[0][1]
        current = self.last_price
        
        if not start:
            return 0.0
        
        return (current - start) / start
    
    def get_rolling_volatility(self, window_seconds=600):
        """Calculate rolling volatility"""
        now = time.time()
        cutoff = now - window_seconds
        recent = [p for t, p in self.prices if t >= cutoff]
        
        if len(recent) < 10:
            return 0.001  # 0.1% default volatility
        
        log_returns = []
        for i in range(1, len(recent)):
            if recent[i-1] > 0 and recent[i] > 0:
                log_returns.append(math.log(recent[i] / recent[i-1]))
        
        if len(log_returns) < 5:
            return 0.001
        
        mean = sum(log_returns) / len(log_returns)
        variance = sum((x - mean) ** 2 for x in log_returns) / len(log_returns)
        vol = math.sqrt(variance)
        
        return max(vol, 0.0001)  # Minimum 0.01% volatility
    
    def get_trend_direction(self, window_seconds=60):
        """Get trend direction (-1, 0, 1)"""
        # Use the simulated trend
        return self.trend
