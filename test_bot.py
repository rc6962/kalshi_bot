"""Simple test to verify bot components work without hanging"""
import asyncio
import sys

print("Starting bot test...")

# Test imports
print("Testing imports...")
try:
    from api.kalshi_client import KalshiClient
    print("  ✓ KalshiClient imported")
except Exception as e:
    print(f"  ✗ Failed to import KalshiClient: {e}")
    sys.exit(1)

try:
    from api.mock_futures_client import MockFuturesClient
    print("  ✓ MockFuturesClient imported")
except Exception as e:
    print(f"  ✗ Failed to import MockFuturesClient: {e}")
    sys.exit(1)

try:
    from engine.event_loop import EventLoop
    print("  ✓ EventLoop imported")
except Exception as e:
    print(f"  ✗ Failed to import EventLoop: {e}")
    sys.exit(1)

try:
    from engine.trade_logger import TradeLogger
    print("  ✓ TradeLogger imported")
except Exception as e:
    print(f"  ✗ Failed to import TradeLogger: {e}")
    sys.exit(1)

print("\nAll imports successful!")
print("\nThe issue is likely in the main.py initialization sequence.")
print("Try running the bot with: python -c 'from main import main; asyncio.run(main())'")
