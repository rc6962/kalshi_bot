import asyncio
import os
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
    api_key = os.getenv("KALSHI_API_KEY")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    
    print("=== Kalshi Position Diagnostics ===")
    print(f"API Key: {api_key[:8]}...")
    print(f"Private Key Path: {private_key_path}")
    print(f"Base URL: {KALSHI_BASE_URL}\n")
    
    kalshi = KalshiClient(api_key, private_key_path, KALSHI_BASE_URL)
    
    try:
        # Try fetching positions WITHOUT count_filter
        print("--- Fetching /portfolio/positions (no filter) ---")
        try:
            raw_pos = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/positions?limit=100")
            print("Response:", raw_pos)
        except Exception as e:
            print("Error on plain positions:", e)
            
        # Try fetching positions WITH count_filter
        print("\n--- Fetching /portfolio/positions (with count_filter) ---")
        try:
            raw_pos_filt = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/positions?count_filter=position&limit=100")
            print("Response:", raw_pos_filt)
        except Exception as e:
            print("Error on filtered positions:", e)
            
        # Try fetching open orders
        print("\n--- Fetching /portfolio/orders ---")
        try:
            raw_orders = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/orders?status=open&limit=100")
            print("Response:", raw_orders)
        except Exception as e:
            print("Error on orders:", e)
            
    except Exception as e:
        print(f"Error checking positions: {e}")
    finally:
        await kalshi.close()

if __name__ == "__main__":
    asyncio.run(main())
