import asyncio
import sys
sys.path.insert(0, 'c:\\Tradingbots\\kalshi_bot')

from api.futures_client import FuturesClient

async def test_connection():
    print("Creating FuturesClient for BTCUSDT...")
    client = FuturesClient('btcusdt')
    
    print("Starting connection to Coinbase...")
    # This will run forever, so we'll run it for 10 seconds and then cancel
    task = asyncio.create_task(client.connect())
    
    # Wait for 10 seconds to see if we get any price updates
    await asyncio.sleep(10)
    
    # Check if we received any prices
    if client.last_price:
        print(f"✓ Success! Received price: ${client.last_price:,.2f}")
        print(f"✓ Price history length: {len(client.prices)}")
    else:
        print("✗ No price updates received in 10 seconds")
    
    # Cancel the connection task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    asyncio.run(test_connection())
