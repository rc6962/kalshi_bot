#!/usr/bin/env python3
"""
SIMPLE BOT - Minimal version that works with Coinbase
No circular imports, no latency_optimizer, no warm_up_connections
"""
import asyncio
import json
import time
import os
from datetime import datetime
import websockets

# ============================================================
# SIMPLIFIED COINBASE CLIENT
# ============================================================
class SimpleCoinbaseClient:
    def __init__(self, symbol):
        self.symbol = symbol
        self.price = None
        self.running = True
        
    async def connect(self):
        """Connect to Coinbase WebSocket and stream prices"""
        url = "wss://advanced-trade-ws.coinbase.com"
        print(f"[Coinbase] Connecting to {url} for {self.symbol}")
        
        async with websockets.connect(url) as ws:
            # Subscribe to ticker
            subscribe = {
                "type": "subscribe",
                "product_ids": [self.symbol],
                "channel": "ticker"
            }
            await ws.send(json.dumps(subscribe))
            print(f"[Coinbase] Subscribed to {self.symbol}")
            
            # Wait for confirmation
            response = await ws.recv()
            print(f"[Coinbase] Response: {response[:200]}...")
            
            # Process messages
            async for message in ws:
                data = json.loads(message)
                if data.get("channel") == "ticker":
                    for event in data.get("events", []):
                        for ticker in event.get("tickers", []):
                            price = float(ticker.get("price", 0))
                            if price > 0:
                                self.price = price
                                print(f"[{self.symbol}] Price: ${price:,.2f}")
                await asyncio.sleep(0.1)
    
    def get_price(self):
        return self.price

# ============================================================
# SIMPLIFIED KALSHI CLIENT (mock for testing)
# ============================================================
class SimpleKalshiClient:
    def __init__(self):
        print("[Kalshi] Mock client initialized")
    
    async def get_balance(self):
        return 10000.0
    
    async def close(self):
        print("[Kalshi] Closed")

# ============================================================
# MAIN BOT
# ============================================================
async def main():
    print("=" * 60)
    print("SIMPLE TRADING BOT - Coinbase Data Feed")
    print("=" * 60)
    
    # Asset mapping: display name -> Coinbase product ID
    assets = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "SOL": "SOL-USD",
    }
    
    print(f"\n📊 Assets to track: {', '.join(assets.keys())}")
    print(f"📡 Price source: Coinbase Advanced Trade (US-accessible)")
    print(f"🛑 Press Ctrl+C to stop\n")
    
    # Initialize Kalshi client
    kalshi = SimpleKalshiClient()
    
    # Start price feeds
    tasks = []
    clients = []
    
    for name, symbol in assets.items():
        client = SimpleCoinbaseClient(symbol)
        clients.append(client)
        task = asyncio.create_task(client.connect())
        tasks.append(task)
        print(f"✅ Started price feed for {name} ({symbol})")
    
    # Wait for first prices
    print("\n⏳ Waiting for initial prices...")
    await asyncio.sleep(3)
    
    # Display current prices
    print("\n📈 Current prices:")
    for i, (name, _) in enumerate(assets.items()):
        price = clients[i].get_price()
        if price:
            print(f"  {name}: ${price:,.2f}")
        else:
            print(f"  {name}: Waiting for data...")
    
    print("\n" + "=" * 60)
    print("✅ BOT RUNNING - Streaming price updates")
    print("=" * 60 + "\n")
    
    # Keep running
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
    finally:
        await kalshi.close()
        print("👋 Bot stopped")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Error: {e}")
        import traceback
        traceback.print_exc()
