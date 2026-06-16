import asyncio
import os
import json
from api.kalshi_client import KalshiClient
from config import KALSHI_BASE_URL

# Load environment variables
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
    
    print("=== Kalshi Balance & Positions Diagnostics ===")
    print(f"API Key: {api_key[:8]}...")
    print(f"Base URL: {KALSHI_BASE_URL}\n")
    
    kalshi = KalshiClient(api_key, private_key_path, KALSHI_BASE_URL)
    
    try:
        # Fetch raw balance
        print("--- Fetching /portfolio/balance ---")
        try:
            raw_balance = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/balance")
            print("Raw Balance Response:", json.dumps(raw_balance, indent=2))
        except Exception as e:
            print("Error fetching balance:", e)
            
        # Fetch raw positions
        print("\n--- Fetching /portfolio/positions ---")
        try:
            raw_pos = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/positions")
            print("Raw Positions Response:", json.dumps(raw_pos, indent=2))
        except Exception as e:
            print("Error fetching positions:", e)
            
    except Exception as e:
        print(f"Diagnostic failed: {e}")
    finally:
        await kalshi.close()

if __name__ == "__main__":
    asyncio.run(main())
