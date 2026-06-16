import asyncio
import websockets
import json

async def test_coinbase():
    url = "wss://advanced-trade-ws.coinbase.com"
    print(f"Connecting to {url}")
    
    async with websockets.connect(url) as ws:
        print("Connected!")
        
        # Subscribe to BTC-USD ticker
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channel": "ticker"
        }
        await ws.send(json.dumps(subscribe_msg))
        print("Subscription sent")
        
        # Wait for subscription confirmation
        response = await ws.recv()
        print(f"Response: {response}")
        
        # Receive 5 ticker updates
        for i in range(5):
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            print(f"Message {i+1}: {data}")
            
            if data.get("channel") == "ticker":
                events = data.get("events", [])
                for event in events:
                    for ticker in event.get("tickers", []):
                        price = ticker.get("price")
                        print(f"BTC-USD Price: ${price}")

if __name__ == "__main__":
    asyncio.run(test_coinbase())
