import asyncio
import websockets

async def test():
    try:
        async with websockets.connect('wss://fstream.binance.com/market/ws/btcusdt@trade') as ws:
            print("Connected! Waiting for data...")
            data = await asyncio.wait_for(ws.recv(), timeout=10)
            print("Received:", data)
            # Keep receiving for a few more messages
            for i in range(3):
                data = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"Message {i+2}:", data)
            print("Received:", data)
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
