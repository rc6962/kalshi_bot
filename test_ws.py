import asyncio
from api.futures_client import FuturesClient

async def test():
    print("Testing Binance WebSocket connection...")
    f = FuturesClient('btcusdt')
    try:
        await asyncio.wait_for(f.connect(), timeout=10)
        await asyncio.sleep(2)
        print(f"Connected! Spot price: ${f.get_spot():,.2f}")
    except asyncio.TimeoutError:
        print("ERROR: Connection timeout after 10 seconds")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await f.close()

if __name__ == "__main__":
    asyncio.run(test())
