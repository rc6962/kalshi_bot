import os
import sys

# Automatically log all terminal output to a file so the assistant can monitor it
class TeeLogger:
    def __init__(self, filename, stream):
        self.stream = stream
        self.log = open(filename, "a", encoding="utf-8")
    
    def write(self, message):
        self.stream.write(message)
        self.stream.flush()
        self.log.write(message)
        self.log.flush()
        
    def flush(self):
        self.stream.flush()
        self.log.flush()

sys.stdout = TeeLogger("bot.log", sys.stdout)
sys.stderr = TeeLogger("bot.log", sys.stderr)

os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
import asyncio
import contextlib
import time
import signal
from datetime import datetime, timezone, timedelta
from api.kalshi_client import KalshiClient
from api.futures_client import FuturesClient
from engine.event_loop import EventLoop
from engine.trade_logger import TradeLogger
from engine.latency_optimizer import latency_tracker, warm_up_connections
from config import ASSET_SYMBOLS, KALSHI_BASE_URL, MAX_POSITIONS_PER_ASSET

def manual_load_dotenv(filepath=".env"):
    """Standard library replacement for python-dotenv."""
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

API_KEY = os.getenv("KALSHI_API_KEY", "d8007edd-1341-4d12-b2ad-fed79d2e2af9")
PRIVATE_KEY_PATH = "kalshi_private_key.pem"

TRADE_LOG_PATH = "trades.csv"


async def initialize_asset_loop(kalshi, asset, futures_symbol, reserve_position, release_position, trade_logger, global_exposures=None, portfolio_positions=None):
    print(f"[{asset}] Initializing asset loop...")
    futures = FuturesClient(futures_symbol)
    loop = EventLoop(
        kalshi,
        futures,
        asset,
        reserve_position=reserve_position,
        release_position=release_position,
        trade_logger=trade_logger,
        global_exposures=global_exposures,
        portfolio_positions=portfolio_positions,
    )
    # Start the WebSocket connection
    await futures.connect()  # Connect first before waiting for price

    try:
        # Wait for first price with timeout (increased from 5 to 60 seconds)
        timeout = 60  # Increased timeout to allow more time for WebSocket connections
        start_time = time.time()
        print(f"[{asset}] Waiting up to {timeout} seconds for futures price...")
        
        # Wait for the WebSocket to receive the first price
        while futures.get_spot() is None:
            if time.time() - start_time > timeout:
                print(f"[{asset}] Timeout waiting for futures price after {timeout} seconds")
                await futures.close()  # Close the WebSocket connection
                raise TimeoutError(f"No futures price received after {timeout} seconds for {asset}")
            
            # Print progress every 10 seconds
            elapsed = time.time() - start_time
            if int(elapsed) % 10 == 0 and elapsed > 0:
                print(f"[{asset}] Still waiting for price data... ({elapsed:.1f}s elapsed)")
                
            await asyncio.sleep(0.5)  # Check every half second

        print(f"[{asset}] Got spot price: ${futures.get_spot():.2f}")
        await loop.initialize()
        print(f"[{asset}] Loop initialized successfully")
        
        # Create a task for the WebSocket connection to keep it running
        ws_task = futures.ws_task if hasattr(futures, 'ws_task') else None
        return loop, ws_task
    except Exception as exc:
        print(f"[{asset}] initialization failed: {exc}")
        await futures.close()  # Ensure WebSocket connection is closed
        return None, None


def get_message_ticker(data):
    ticker_data = data.get("data") or data.get("msg") or {}
    return (
        ticker_data.get("market_ticker")
        or ticker_data.get("ticker")
        or ticker_data.get("event_ticker")
    )


def next_midnight_eastern():
    now = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-4) if time.localtime().tm_isdst else timedelta(hours=-5)
    now_et = now + et_offset
    midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return (midnight_et - now_et).total_seconds()


async def eod_export_loop(trade_logger):
    while True:
        seconds_till_midnight = next_midnight_eastern()
        await asyncio.sleep(seconds_till_midnight)
        print("End of day reached; exporting daily report...")
        try:
            from scripts.export_report import export_google_sheet_pdf
            sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
            creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
            if sheet_id and sheet_id != "your_google_sheet_id_here" and os.path.exists(creds_path):
                export_google_sheet_pdf(sheet_id, creds_path)
            else:
                print("Google Sheets integration not configured. Please set GOOGLE_SHEET_ID in .env and ensure google_credentials.json exists.")
        except Exception as e:
            print(f"EOD export failed: {e}")
        trade_logger.daily_trades.clear()


async def balance_loop(kalshi, trade_logger):
    while True:
        try:
            balance = await kalshi.get_balance()
            if balance is not None:
                trade_logger.update_portfolio_value(balance)
        except Exception:
            pass
        await asyncio.sleep(60)


async def latency_report_loop():
    """Periodically print latency statistics."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        latency_tracker.print_report()


async def main():
    print("DEBUG: Starting main()")
    manual_load_dotenv()
    print("DEBUG: Loaded .env")
    print("BASE URL:", KALSHI_BASE_URL)
    print("DEBUG: Creating KalshiClient...")
    kalshi = KalshiClient(API_KEY, PRIVATE_KEY_PATH, KALSHI_BASE_URL)
    print("DEBUG: KalshiClient created")
    
    # Warm up connections before starting trading
    print("Warming up connections...")
    await warm_up_connections(KALSHI_BASE_URL)
    print("Warm-up complete")
    
    trade_logger = TradeLogger(TRADE_LOG_PATH)
    
    # Fetch initial balance to prevent sizer from defaulting to fallback capital
    initial_balance = None
    try:
        initial_balance = await kalshi.get_balance()
        if initial_balance is not None:
            print(f"Initial portfolio balance: ${initial_balance:.2f}")
    except Exception as exc:
        print(f"Failed to fetch initial balance: {exc}")

    # Try to initialize Google Sheets right away to check if it's properly configured
    trade_logger.try_init_sheet()
    
    # Shared state for portfolio balance & positions
    portfolio_balance = {"value": initial_balance}
    
    # Fetch initial positions once on startup to avoid concurrent API hits during asset loop initialization
    initial_positions = []
    try:
        initial_positions = await kalshi.get_open_positions()
        print(f"Initial portfolio positions synced: {len(initial_positions)} active positions")
    except Exception as exc:
        print(f"Failed to fetch initial positions: {exc}")
        
    portfolio_positions = {"value": initial_positions}
    
    loops = []
    portfolio_lock = asyncio.Lock()
    positions_per_asset = {asset: 0 for asset in ASSET_SYMBOLS.keys()}
    global_exposures = {asset: 0.0 for asset in ASSET_SYMBOLS.keys()}

    async def reserve_position(asset, contracts):
        async with portfolio_lock:
            if positions_per_asset[asset] >= MAX_POSITIONS_PER_ASSET:
                print(
                    f"[{asset}] Position cap reached:",
                    f"positions={positions_per_asset[asset]}",
                    f"max={MAX_POSITIONS_PER_ASSET}"
                )
                return False
            positions_per_asset[asset] += 1
            return True

    def release_position(asset, contracts):
        positions_per_asset[asset] = max(0, positions_per_asset[asset] - 1)

    # Initialize sequentially with a delay to avoid hitting Kalshi 429 rate limits
    initialized = []
    for asset, futures_symbol in ASSET_SYMBOLS.items():
        try:
            result = await initialize_asset_loop(
                kalshi, asset, futures_symbol, reserve_position, release_position, trade_logger, global_exposures, portfolio_positions
            )
            initialized.append(result)
        except Exception as e:
            initialized.append(e)
        await asyncio.sleep(1.5)  # 1.5 second delay to stay safe from 429 rate limits

    # Process results and handle exceptions
    loops = []
    futures_tasks = []
    successful_assets = []
    
    for i, result in enumerate(initialized):
        asset = list(ASSET_SYMBOLS.keys())[i]
        if isinstance(result, Exception):
            print(f"[{asset}] Initialization failed with exception: {result}")
            continue
        
        if result is None:
            print(f"[{asset}] Initialization returned None values")
            continue
            
        loop, task = result
        if loop is not None and task is not None:
            loops.append(loop)
            futures_tasks.append(task)
            successful_assets.append(asset)
        else:
            print(f"[{asset}] Initialization returned None values")
    
    print(f"Successfully initialized {len(successful_assets)} out of {len(ASSET_SYMBOLS)} assets: {successful_assets}")

    if not loops:
        print("ERROR: No asset loops initialized successfully. Exiting.")
        return  # Exit gracefully instead of raising an exception

    ticker_to_loop = {loop.current_ticker: loop for loop in loops if hasattr(loop, 'current_ticker')}
    subscription_changed = asyncio.Event()

    async def handle_routed_ticker(loop, data):
        if loop._ticker_in_flight:
            return
        loop._ticker_in_flight = True
        try:
            await loop.handle_ticker(data)
        except Exception as exc:
            print(f"[{loop.asset}] ticker handler failed:", exc)
        finally:
            loop._ticker_in_flight = False

    async def route_ticker(data):
        ticker = get_message_ticker(data)
        loop = ticker_to_loop.get(ticker)
        if loop is None:
            print("Ignoring Kalshi ticker for unsubscribed market:", ticker)
            return
        asyncio.create_task(handle_routed_ticker(loop, data))

    async def market_rollover_monitor():
        while True:
            await asyncio.sleep(5)
            for loop in loops:
                if not loop.market_expired():
                    continue

                old_ticker = loop.current_ticker
                changed = await loop.rollover_market()
                if not changed:
                    continue

                ticker_to_loop.pop(old_ticker, None)
                ticker_to_loop[loop.current_ticker] = loop
                subscription_changed.set()

    async def shared_websocket_runner():
        while True:
            tickers = list(ticker_to_loop.keys())
            print("Starting shared Kalshi websocket for:", tickers)
            subscription_changed.clear()

            ws_task = asyncio.create_task(kalshi.websocket_listen(tickers, route_ticker))
            change_task = asyncio.create_task(subscription_changed.wait())

            done, pending = await asyncio.wait(
                [ws_task, change_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if change_task in done:
                print("Kalshi subscription changed; restarting shared websocket.")
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ws_task
            else:
                change_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await change_task
                try:
                    await ws_task
                except Exception as exc:
                    print("Shared Kalshi websocket failed; reconnecting:", exc)
                    await asyncio.sleep(2)

            for task in pending:
                task.cancel()

    # Wire balance into TradeLogger for reference
    if trade_logger is not None:
        trade_logger._portfolio_balance_ref = portfolio_balance

    # Central sync loop for balance and positions
    async def portfolio_sync_loop():
        last_balance_sync = time.time()
        while True:
            await asyncio.sleep(5)  # Wait 5s between syncs, avoiding redundant immediate queries on startup
            # Sync positions every 5 seconds
            try:
                positions = await kalshi.get_open_positions()
                portfolio_positions["value"] = positions
            except Exception as e:
                print(f"[Portfolio] Positions sync failed: {e}")
                
            # Sync balance every 60 seconds
            now = time.time()
            if now - last_balance_sync >= 60:
                try:
                    balance = await kalshi.get_balance()
                    if balance is not None:
                        portfolio_balance["value"] = balance
                        if trade_logger is not None:
                            trade_logger.update_portfolio_value(balance)
                        print(f"[Portfolio] Balance updated: ${balance:.2f}")
                        last_balance_sync = now
                except Exception as e:
                    print(f"[Portfolio] Balance sync failed: {e}")
            await asyncio.sleep(5)

    portfolio_task = asyncio.create_task(portfolio_sync_loop())
    rollover_task = asyncio.create_task(market_rollover_monitor())
    ws_runner_task = asyncio.create_task(shared_websocket_runner())
    eod_task = asyncio.create_task(eod_export_loop(trade_logger))
    latency_task = asyncio.create_task(latency_report_loop())
    
    # Pass balance ref to all event loops for dynamic position sizing
    for loop_obj in loops:
        loop_obj._portfolio_balance_ref = portfolio_balance
        # Also wire into risk manager → kelly_sizer for portfolio-based contract sizing
        loop_obj.risk.set_balance_ref(portfolio_balance)
    
    # Handle graceful shutdown (Windows-compatible)
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        print("\nShutdown signal received, closing gracefully...")
        shutdown_event.set()
    
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
    except (NotImplementedError, AttributeError):
        pass
    try:
        loop.add_signal_handler(signal.SIGINT, signal_handler)
    except (NotImplementedError, AttributeError):
        pass
    
    try:
        await asyncio.gather(
            *futures_tasks, 
            rollover_task, 
            ws_runner_task, 
            eod_task, 
            portfolio_task,
            latency_task,
        )
    finally:
        # Clean up connections
        await kalshi.close()
        print("Connections closed.")


if __name__ == "__main__":
    asyncio.run(main())