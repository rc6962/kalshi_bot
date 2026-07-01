# SECURITY: Fail fast if Python 2 syntax sneaks in (Windows line ending corruption)
import ast
import sys

with open(__file__) as _f:
    _src = _f.read()
try:
    ast.parse(_src)
except SyntaxError as _exc:
    sys.exit(f"Python 3 syntax error in main.py: {_exc}")

import os

# Monkeypatch platform.system and platform.uname to prevent hangs on Windows when importing aiohttp
import platform
import sys
from collections import deque

platform.system = lambda: "Windows"
from collections import namedtuple

uname_result = namedtuple(
    "uname_result", ["system", "node", "release", "version", "machine", "processor"]
)
platform.uname = lambda: uname_result(
    system="Windows",
    node="localhost",
    release="10",
    version="10.0.0",
    machine="AMD64",
    processor="Intel",
)


# Automatically log all terminal output to a file so the assistant can monitor it
# Bounded to the last ~50,000 print chunks to prevent log bloating.
class TeeLogger:
    _buffer = deque(maxlen=50000)
    _write_count = 0

    def __init__(self, filename, stream):
        self.stream = stream
        self.filename = filename

    def write(self, message):
        safe = (
            message.encode(self.stream.encoding or "utf-8", errors="replace").decode(
                self.stream.encoding or "utf-8"
            )
            if hasattr(self.stream, "encoding")
            else message
        )
        self.stream.write(safe)
        self.stream.flush()
        TeeLogger._buffer.append(message)  # store original (utf-8) for file log
        TeeLogger._write_count += 1

        if TeeLogger._write_count >= 50:
            TeeLogger._write_count = 0
            self.flush_to_file()

    def flush(self):
        self.stream.flush()
        self.flush_to_file()

    def flush_to_file(self):
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                f.write("".join(TeeLogger._buffer))
        except Exception:
            pass


sys.stdout = TeeLogger("bot.log", sys.stdout)
sys.stderr = TeeLogger("bot.log", sys.stderr)

os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
import asyncio
import contextlib
import json
import signal
import time
import traceback
from datetime import datetime, timedelta, timezone

from api.futures_client import FuturesClient
from api.futures_coinbase import CoinbaseClient
from api.kalshi_client import KalshiClient
from config import (
    ASSET_SYMBOLS,
    COINBASE_SYMBOL_MAP,
    ENABLE_CFB_RTI,
    KALSHI_BASE_URL,
    KALSHI_DEMO_BASE_URL,
    SANDBOX_MODE,
    USE_COINBASE_ADVANCED,
)
from dashboard_server import start_dashboard
from engine.event_loop import EventLoop
from engine.inter_window_carry import IWMCManager
from engine.latency_optimizer import latency_tracker, warm_up_connections
from engine.trade_logger import TradeLogger


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


manual_load_dotenv()
if SANDBOX_MODE:
    API_KEY = os.getenv("KALSHI_DEMO_API_KEY") or os.getenv("KALSHI_API_KEY")
    PRIVATE_KEY_PATH = os.getenv(
        "KALSHI_DEMO_PRIVATE_KEY_PATH", "kalshi_demo_private_key.pem"
    )
    print(f"\033[93m[SANDBOX] Using demo API credentials (key={API_KEY[:8]}...)\033[0m")
else:
    API_KEY = os.getenv("KALSHI_API_KEY")
    PRIVATE_KEY_PATH = "kalshi_private_key.pem"

TRADE_LOG_PATH = "trades.csv"


async def initialize_asset_loop(
    kalshi,
    asset,
    futures_symbol,
    coinbase_symbol,
    trade_logger,
    global_exposures=None,
    global_reservations=None,
    portfolio_positions=None,
    iwmc_manager=None,
    coinbase_advanced_manager=None,
):
    print(f"[{asset}] Initializing asset loop...")
    futures = FuturesClient(futures_symbol)
    # Use Advanced Trade client if manager provided; else legacy public feed
    if coinbase_advanced_manager is not None and coinbase_symbol:
        coinbase = coinbase_advanced_manager.get_client_for_asset(coinbase_symbol)
        if coinbase is None:
            print(
                f"[{asset}] Warning: no Advanced client for {coinbase_symbol}, falling back to legacy"
            )
            coinbase = CoinbaseClient(coinbase_symbol) if coinbase_symbol else None
    else:
        coinbase = CoinbaseClient(coinbase_symbol) if coinbase_symbol else None
    loop = EventLoop(
        kalshi,
        futures,
        coinbase_client=coinbase,
        asset=asset,
        trade_logger=trade_logger,
        global_exposures=global_exposures,
        global_reservations=global_reservations,
        portfolio_positions=portfolio_positions,
    )

    # Attach IWMC manager for cross-asset coordination
    if iwmc_manager:
        loop.iwmc_manager = iwmc_manager
    # Start the WebSocket connections
    await futures.connect()  # Connect first before waiting for price

    try:
        # Wait for first price with timeout (increased from 5 to 60 seconds)
        timeout = 60  # Increased timeout to allow more time for WebSocket connections
        start_time = time.time()
        print(f"[{asset}] Waiting up to {timeout} seconds for futures price...")

        # Wait for the WebSocket to receive the first price
        while futures.get_spot() is None:
            if time.time() - start_time > timeout:
                print(
                    f"[{asset}] Timeout waiting for futures price after {timeout} seconds"
                )
                await futures.close()  # Close the WebSocket connection
                raise TimeoutError(
                    f"No futures price received after {timeout} seconds for {asset}"
                )

            # Print progress every 10 seconds
            elapsed = time.time() - start_time
            if int(elapsed) % 10 == 0 and elapsed > 0:
                print(
                    f"[{asset}] Still waiting for price data... ({elapsed:.1f}s elapsed)"
                )

            await asyncio.sleep(0.5)  # Check every half second

        print(f"[{asset}] Got spot price: ${futures.get_spot():.2f}")
        await loop.initialize()
        print(f"[{asset}] Loop initialized successfully")

        # Create a task for the WebSocket connection to keep it running
        ws_task = futures.ws_task if hasattr(futures, "ws_task") else None
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
    et_offset = (
        timedelta(hours=-4) if time.localtime().tm_isdst else timedelta(hours=-5)
    )
    now_et = now + et_offset
    midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
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
            if (
                sheet_id
                and sheet_id != "your_google_sheet_id_here"
                and os.path.exists(creds_path)
            ):
                export_google_sheet_pdf(sheet_id, creds_path)
            else:
                print(
                    "Google Sheets integration not configured. Please set GOOGLE_SHEET_ID in .env and ensure google_credentials.json exists."
                )
        except Exception as e:
            print(f"EOD export failed: {e}")
        trade_logger.daily_trades.clear()


async def latency_report_loop():
    """Periodically print latency statistics."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        latency_tracker.print_report()


async def main():
    print("DEBUG: Starting main()")
    manual_load_dotenv()
    print("DEBUG: Loaded .env")
    base_url = KALSHI_DEMO_BASE_URL if SANDBOX_MODE else KALSHI_BASE_URL
    print("BASE URL:", base_url)
    if SANDBOX_MODE:
        print(
            "\033[93m[SANDBOX] Running against demo.kalshi.co — orders go to sandbox API\033[0m"
        )
    print("DEBUG: Creating KalshiClient...")
    kalshi = KalshiClient(API_KEY, PRIVATE_KEY_PATH, base_url)
    print("DEBUG: KalshiClient created")

    # Warm up connections before starting trading
    print("Warming up connections...")
    await warm_up_connections(base_url)
    print("Warm-up complete")

    trade_logger = TradeLogger(TRADE_LOG_PATH)

    # CF Benchmarks RTI index IDs — subscribe all available indices
    # HYPE and BNB omitted unless CFB indices are confirmed
    CFB_INDEX_IDS = [
        "BRTI",  # BTC
        "ETHUSD_RTI",  # ETH
        "SOLUSD_RTI",  # SOL
        "XRPUSD_RTI",  # XRP
        "DOGEUSD_RTI",  # DOGE
    ]

    # CF Benchmarks callback — feeds into shared state for signal engine consumption
    from feed.cfb_state import update as cfb_update

    async def handle_cfb_valuation(data):
        msg = data.get("msg") or {}
        index_id = msg.get("index_id")
        raw_json = msg.get("data")
        value = None
        if isinstance(raw_json, str):
            try:
                parsed = json.loads(raw_json)
                value = parsed.get("value")
            except Exception:
                pass
        avg_60s = None
        if isinstance(msg.get("avg_60s_data"), dict):
            avg_60s = msg["avg_60s_data"].get("value")
        if index_id and value:
            cfb_update(index_id, float(value), float(avg_60s) if avg_60s else None)
            # DEBUG: confirm CFB data is flowing
            if not hasattr(handle_cfb_valuation, "_count"):
                handle_cfb_valuation._count = 0
            handle_cfb_valuation._count += 1
            if handle_cfb_valuation._count <= 3:
                print(
                    f"[CFB DEBUG] #{handle_cfb_valuation._count} {index_id}={value} avg60={avg_60s}"
                )

    if ENABLE_CFB_RTI:
        kalshi.cfb_callback = handle_cfb_valuation

    # Fetch initial balance to prevent sizer from defaulting to fallback capital
    initial_balance = None
    try:
        initial_balance = await kalshi.get_balance()
        if initial_balance is not None:
            print(f"Initial portfolio balance: ${initial_balance:.2f}")
    except Exception as exc:
        print(f"Failed to fetch initial balance: {exc}")

    # Shared state for portfolio balance & positions
    portfolio_balance = {"value": initial_balance}

    # Fetch initial positions once on startup to avoid concurrent API hits during asset loop initialization
    initial_positions = []
    try:
        initial_positions = await kalshi.get_open_positions()
        print(
            f"Initial portfolio positions synced: {len(initial_positions)} active positions"
        )
    except Exception as exc:
        print(f"Failed to fetch initial positions: {exc}")

    portfolio_positions = {"value": initial_positions}

    # Initialize IWMC manager for cross-asset coordination
    from engine.inter_window_carry import IWMCManager

    iwmc_manager = IWMCManager(list(ASSET_SYMBOLS.keys()))

    # Initialize Coinbase Advanced manager (if enabled)
    coinbase_advanced_manager = None
    if USE_COINBASE_ADVANCED:
        try:
            from api.coinbase_advanced_manager import CoinbaseAdvancedManager

            coinbase_advanced_manager = CoinbaseAdvancedManager()
            print("[Coinbase] Advanced Trade Manager initialized")
        except Exception as exc:
            print(
                f"[Coinbase] Advanced Trade Manager failed to init: {exc}. Falling back to legacy."
            )

    loops = []
    global_exposures = {asset: 0.0 for asset in ASSET_SYMBOLS.keys()}
    global_reservations = {}

    # Initialize ALL assets in PARALLEL (not sequentially)
    print(f"Initializing {len(ASSET_SYMBOLS)} asset loops in parallel...")

    async def init_single_asset(asset, futures_symbol, coinbase_symbol):
        try:
            result = await initialize_asset_loop(
                kalshi,
                asset,
                futures_symbol,
                coinbase_symbol,
                trade_logger,
                global_exposures,
                global_reservations,
                portfolio_positions,
                iwmc_manager,
                coinbase_advanced_manager,
            )
            return asset, result
        except Exception as e:
            return asset, e

    # Launch all initializations concurrently
    init_tasks = []
    for asset, futures_symbol in ASSET_SYMBOLS.items():
        coinbase_symbol = COINBASE_SYMBOL_MAP.get(asset)
        init_tasks.append(init_single_asset(asset, futures_symbol, coinbase_symbol))

    # Wait for all to complete
    initialized = await asyncio.gather(*init_tasks, return_exceptions=True)

    # Process results
    loops = []
    futures_tasks = []
    successful_assets = []

    for result in initialized:
        if isinstance(result, Exception):
            print(f"Initialization failed with exception: {result}")
            continue

        asset, result = result
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

    print(
        f"Successfully initialized {len(successful_assets)} out of {len(ASSET_SYMBOLS)} assets: {successful_assets}"
    )

    if not loops:
        print("ERROR: No asset loops initialized successfully. Exiting.")
        return  # Exit gracefully instead of raising an exception

    ticker_to_loop = {
        loop.current_ticker: loop for loop in loops if hasattr(loop, "current_ticker")
    }
    subscription_changed = asyncio.Event()

    async def handle_routed_ticker(loop, data):
        if loop._ticker_in_flight:
            return
        loop._ticker_in_flight = True
        try:
            await loop.handle_ticker(data)
        except Exception as exc:
            print(f"[{loop.asset}] ticker handler failed:", exc)
            traceback.print_exc()
        finally:
            loop._ticker_in_flight = False

    async def route_ticker(data):
        msg_type = data.get("type")

        # For orderbook/cfb messages, ticker is in the nested msg sub-dict
        if msg_type in ("orderbook_snapshot", "orderbook_delta"):
            ticker_data = data.get("msg") or {}
            ticker = (
                ticker_data.get("market_ticker")
                or ticker_data.get("ticker")
                or data.get("market_ticker")
                or data.get("ticker")
            )
        elif msg_type == "cfbenchmarks_value":
            # CFB handled by cfb_callback in websocket_listen — no routing needed
            return
        else:
            ticker = get_message_ticker(data)

        loop = ticker_to_loop.get(ticker)
        if loop is None:
            return
        asyncio.create_task(handle_routed_ticker(loop, data))

    async def market_rollover_monitor():
        while True:
            await asyncio.sleep(5)
            for loop in loops:
                if not loop.market_expired():
                    continue

                old_ticker = loop.current_ticker
                try:
                    changed = await loop.rollover_market()
                except Exception as e:
                    print(f"[{loop.asset}] Rollover monitor error: {e}")
                    continue
                if not changed:
                    continue

                ticker_to_loop.pop(old_ticker, None)
                ticker_to_loop[loop.current_ticker] = loop
                subscription_changed.set()

    async def shared_websocket_runner():
        ws_backoff = 0
        while True:
            tickers = list(ticker_to_loop.keys())
            print("Starting shared Kalshi websocket for:", tickers)
            subscription_changed.clear()

            ws_index_ids = CFB_INDEX_IDS if ENABLE_CFB_RTI else None
            ws_task = asyncio.create_task(
                kalshi.websocket_listen(tickers, route_ticker, index_ids=ws_index_ids)
            )
            change_task = asyncio.create_task(subscription_changed.wait())

            done, pending = await asyncio.wait(
                [ws_task, change_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if change_task in done:
                print("Kalshi subscription changed; restarting shared websocket.")
                ws_task.cancel()
                ws_backoff = 0
                with contextlib.suppress(asyncio.CancelledError):
                    await ws_task
            else:
                change_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await change_task
                try:
                    await ws_task
                except Exception as exc:
                    delay = min(30, 2 * (1.5**ws_backoff))
                    ws_backoff += 1
                    print(
                        f"Shared Kalshi websocket failed; reconnecting in {delay:.0f}s:",
                        exc,
                    )
                    await asyncio.sleep(delay)

            for task in pending:
                task.cancel()

    # Wire balance into TradeLogger for reference
    if trade_logger is not None:
        trade_logger._portfolio_balance_ref = portfolio_balance

    # Central sync loop for balance and positions
    async def portfolio_sync_loop():
        last_balance_sync = time.time()
        pos_backoff = 0
        while True:
            delay = min(30, 5 * (1.5**pos_backoff)) if pos_backoff > 0 else 5
            await asyncio.sleep(delay)
            # Sync positions with exponential backoff on failure
            try:
                positions = await kalshi.get_open_positions()
                if positions is None:
                    pos_backoff += 1
                    print(
                        f"[Portfolio] Positions sync failed (API error, attempt {pos_backoff}). Backing off {min(30, 5 * (1.5**pos_backoff)):.0f}s"
                    )
                else:
                    portfolio_positions["value"] = positions
                    pos_backoff = 0
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                pos_backoff += 1
                print(
                    f"[Portfolio] Positions sync failed ({pos_backoff}): {type(e).__name__}. Backing off {min(30, 5 * (1.5**pos_backoff)):.0f}s"
                )

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

    async def watchdog_loop():
        while True:
            if os.path.exists("restart_flag.txt"):
                print("Restart flag detected. Exiting to allow auto-restart...")
                try:
                    os.remove("restart_flag.txt")
                except Exception:
                    pass
                os._exit(1)
            await asyncio.sleep(5)

    portfolio_task = asyncio.create_task(portfolio_sync_loop())
    rollover_task = asyncio.create_task(market_rollover_monitor())
    ws_runner_task = asyncio.create_task(shared_websocket_runner())
    eod_task = asyncio.create_task(eod_export_loop(trade_logger))
    latency_task = asyncio.create_task(latency_report_loop())
    watchdog_task = asyncio.create_task(watchdog_loop())
    dashboard_task = asyncio.create_task(
        start_dashboard(loops, portfolio_balance, portfolio_positions, port=8555)
    )

    # Pass balance ref and live API positions to all event loops for dynamic bounds checking
    for loop_obj in loops:
        loop_obj._portfolio_balance_ref = portfolio_balance
        loop_obj._portfolio_positions_ref = portfolio_positions
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

    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        await asyncio.gather(
            shutdown_task,
            *futures_tasks,
            rollover_task,
            ws_runner_task,
            eod_task,
            portfolio_task,
            latency_task,
            watchdog_task,
            dashboard_task,
        )
    finally:
        # Clean up connections
        await kalshi.close()
        print("Connections closed.")


if __name__ == "__main__":
    # Startup lock: prevent duplicate bot instances that cause double orders
    lock_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.lock")

    def cleanup_lock():
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
        except Exception:
            pass

    if os.path.exists(lock_file):
        # Check if the PID in the lock file is still running
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())

            # Use tasklist to check if process exists
            import subprocess

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old_pid}"], capture_output=True, text=True
            )
            if str(old_pid) in result.stdout:
                print(
                    f"ERROR: Bot is already running (PID {old_pid}). "
                    f"Cannot start a second instance. Kill PID {old_pid} first."
                )
                sys.exit(1)
            else:
                # Stale lock file from crashed/killed process — clean it up
                print(f"Stale lock file (PID {old_pid} not running). Cleaning up...")
                cleanup_lock()
        except (ValueError, FileNotFoundError, OSError, Exception):
            cleanup_lock()

    # Write our PID to the lock file
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))

    # Clean up lock file on normal exit
    try:
        import atexit

        atexit.register(cleanup_lock)
    except Exception:
        pass

    asyncio.run(main())
