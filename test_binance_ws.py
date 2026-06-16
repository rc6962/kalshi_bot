import asyncio
import websockets
import json

async def test_binance_ws(symbol):
    """Test Binance WebSocket connection directly"""
    urls = [
        f"wss://fstream.binance.com/ws/{symbol}@ticker",
        f"wss://stream.binance.com:9443/ws/{symbol}@ticker",
        f"wss://fstream.binance.com/ws/{symbol}@miniTicker",
        f"wss://fstream.binance.com/ws/{symbol}@bookTicker",
    ]
    
    for idx, url in enumerate(urls):
        print(f"Testing URL {idx+1}: {url}")
        try:
            async with websockets.connect(url) as ws:
                print(f"  ✓ Connected successfully")
                
                # Wait for first message
                print("  Waiting for first message...")
                for i in range(10):  # Try for 10 messages
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(msg)
                        print(f"  ✓ Received message: {data}")
                        
                        # Check if this contains price data
                        if 'c' in data or 'b' in data or 'a' in data or 'p' in data:
                            price = data.get('c') or data.get('b') or data.get('a') or data.get('p')
                            print(f"  ✓ Found price data: {price}")
                            return True
                        else:
                            print(f"  Message doesn't contain expected price fields: {list(data.keys())}")
                    except asyncio.TimeoutError:
                        print(f"  ✗ Timeout waiting for message")
                        continue
                
                print(f"  ✗ No price data received after 10 messages")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    return False

async def main():
    symbols = ['btcusdt', 'ethusdt', 'solusdt', 'dogeusdt', 'xrpusdt', 'hypeusdt']
    
    print("Testing Binance WebSocket connectivity...\n")
    
    for symbol in symbols:
        print(f"Testing {symbol.upper()}:")
        success = await test_binance_ws(symbol)
        if success:
            print(f"  ✓ {symbol.upper()} working\n")
        else:
            print(f"  ✗ {symbol.upper()} not working\n")
    
    print("Test completed.")

if __name__ == "__main__":
    asyncio.run(main())