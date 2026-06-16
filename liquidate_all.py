import asyncio
import os
import sys
from api.kalshi_client import KalshiClient

async def main():
    print("=== Kalshi Emergency Liquidation ===")
    client = KalshiClient()
    
    print("Fetching all open positions...")
    positions = await client.get_open_positions()
    
    if not positions:
        print("No open positions found! Either they were already closed, or they expired and are awaiting settlement.")
        return
        
    print(f"Found {len(positions)} open positions. Attempting to liquidate all...")
    
    for pos in positions:
        ticker = pos.get("market_ticker") or pos.get("ticker")
        side = pos.get("side") or pos.get("position_side")
        contracts = pos.get("position") or pos.get("count")
        
        print(f"\nTargeting: {ticker}")
        print(f"Current Position: {contracts} contracts on {side.upper()}")
        
        try:
            print(f"Submitting market order to CLOSE position on {ticker}...")
            # We pass exit_price=None to ensure it executes at market price
            result = await client.close_position(ticker, exit_price=None)
            print(f"Close result: {result}")
        except Exception as e:
            print(f"Failed to close {ticker}: {e}")
            
    print("\nLiquidation complete. Please check your Kalshi dashboard.")

if __name__ == "__main__":
    asyncio.run(main())
