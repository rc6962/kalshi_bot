# test_demo_vs_prod.py
import asyncio
import os
import sys

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.kalshi_client import KalshiClient

def load_env():
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

async def test_env(name, api_key, private_key_path, url):
    print(f"\n=== Testing {name} Environment ({url}) ===")
    try:
        kalshi = KalshiClient(api_key, private_key_path, url)
        
        # Test 1: Get Balance
        try:
            balance = await kalshi.get_balance()
            print(f"[{name}] Balance connection: SUCCESS. Balance: ${balance}" if balance is not None else f"[{name}] Balance connection: SUCCESS but balance was None")
        except Exception as e:
            print(f"[{name}] Balance connection: FAILED ({e})")
            
        # Test 2: Get Positions
        try:
            raw_response = await kalshi.authenticated_request("GET", "/trade-api/v2/portfolio/positions")
            print(f"[{name}] Raw positions response: {raw_response}")
            
            positions = await kalshi.get_open_positions()
            print(f"[{name}] Filtered positions: {positions}")
        except Exception as e:
            print(f"[{name}] Positions check: FAILED ({e})")
            
        await kalshi.close()
    except Exception as e:
        print(f"[{name}] Init failed: {e}")

async def main():
    load_env()
    api_key = os.getenv("KALSHI_API_KEY")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    
    if not api_key:
        print("ERROR: KALSHI_API_KEY not found in env.")
        return

    # Test Production
    await test_env("PROD (Live)", api_key, private_key_path, "https://external-api.kalshi.com/trade-api/v2")
    
    # Test Demo
    await test_env("DEMO", api_key, private_key_path, "https://demo-api.kalshi.co/trade-api/v2")

if __name__ == "__main__":
    asyncio.run(main())
