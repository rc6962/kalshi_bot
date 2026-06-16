#!/usr/bin/env python3
"""
Simplified bot runner that avoids circular import issues.
Uses the real Kalshi client and Coinbase WebSocket for price data.
"""
import asyncio
import sys
import os

# Add the current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the real clients
from api.kalshi_client import KalshiClient
from api.futures_client import FuturesClient
from engine.event_loop import EventLoop
from engine.trade_logger import TradeLogger
from config import ASSET_SYMBOLS, KALSHI_BASE_URL, MAX_POSITIONS_PER_ASSET

# Load environment variables
def load_env():
    """Load environment variables from .env file"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

async def main():
    print("=== Starting Trading Bot ===\n")
    
    # Load environment variables
    load_env()
    
    # Get API key from environment or use default
    api_key = os.getenv("KALSHI_API_KEY")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    
    print(f"Using Kalshi API endpoint: {KALSHI_BASE_URL}")
    print(f"Private key path: {private_key_path}")
    
    # Initialize Kalshi client
    kalshi = KalshiClient(api_key, private_key_path, KALSHI_BASE_URL)
    
    # Initialize trade logger
    trade_logger = TradeLogger("trades.csv")
    
    # Position tracking
    portfolio_lock = asyncio.Lock()
    positions_per_asset = {asset: 0 for asset in ASSET_SYMBOLS.keys()}
    
    async def reserve_position(asset, contracts):
        async with portfolio_lock:
            if positions_per_asset[asset] >= MAX_POSITIONS_PER_ASSET:
                print(f"[{asset}] Position cap reached: {positions_per_asset[asset]}/{MAX_POSITIONS_PER_ASSET}")
                return False
            positions_per_asset[asset] += 1
            return True
    
    def release_position(asset, contracts):
        positions_per_asset[asset] = max(0, positions_per_asset[asset] - 1)
    
    # Initialize asset loops
    loops = []
    ws_tasks = []
    
    for asset, futures_symbol in ASSET_SYMBOLS.items():
        print(f"\nInitializing {asset} with symbol {futures_symbol}...")
        
        # Create futures client
        futures = FuturesClient(futures_symbol)
        
        # Create event loop
        loop = EventLoop(
            kalshi,
            futures,
            asset,
            reserve_position=reserve_position,
            release_position=release_position,
            trade_logger=trade_logger
        )
        
        # Start WebSocket connection
        ws_task = asyncio.create_task(futures.connect())
        
        # Wait for first price (increased timeout from 30 to 60 seconds)
        timeout = 60  # Increased timeout to allow more time for WebSocket connections
        start_time = asyncio.get_event_loop().time()
        print(f"[{asset}] Waiting up to {timeout} seconds for futures price...")
        
        while not futures.get_spot():
            if ws_task.done():
                exc = ws_task.exception()
                if exc:
                    print(f"[{asset}] WebSocket task failed during initialization: {exc}")
                    raise exc
                raise RuntimeError(f"Websocket task for {asset} exited unexpectedly without receiving prices")
            
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                print(f"[{asset}] Timeout waiting for futures price after {elapsed:.1f} seconds")
                # Cancel the WebSocket task to clean up
                ws_task.cancel()
                try:
                    await ws_task
                except asyncio.CancelledError:
                    pass
                raise TimeoutError(f"No futures price received after {timeout} seconds for {asset}")
            
            # Print progress every 10 seconds
            if int(elapsed) % 10 == 0 and elapsed > 0:
                print(f"[{asset}] Still waiting for price data... ({elapsed:.1f}s elapsed)")
                
            await asyncio.sleep(0.1)
        
        # Initialize the loop
        await loop.initialize()
        
        loops.append(loop)
        ws_tasks.append(ws_task)
        
        print(f"  ✓ Initialized with price: ${futures.get_spot():,.2f}")