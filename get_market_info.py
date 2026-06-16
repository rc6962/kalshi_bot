import asyncio
import os
import json
from api.kalshi_client import KalshiClient
from config import KALSHI_BASE_URL

def load_env():
    env_file = '.env'
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

async def main():
    load_env()
    api_key = os.getenv("KALSHI_API_KEY", "d8007edd-1341-4d12-b2ad-fed79d2e2af9")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    
    kalshi = KalshiClient(api_key, private_key_path, KALSHI_BASE_URL)
    
    try:
        # Get events for SOL
        print("Fetching events for KXSOL15M...")
        data = await kalshi.authenticated_request("GET", "/trade-api/v2/events?series_ticker=KXSOL15M&with_nested_markets=true&limit=5")
        
        with open("market_info.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print("Successfully wrote market_info.json")
    except Exception as e:
        print(f"Error checking market info: {e}")
    finally:
        # Close session if it exists
        if hasattr(kalshi, '_session') and kalshi._session:
            await kalshi._session.close()

if __name__ == "__main__":
    asyncio.run(main())
