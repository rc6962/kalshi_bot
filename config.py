# config.py

ASSET_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "DOGE": "dogeusdt",
    "XRP": "xrpusdt",
    "HYPE": "hypeusdt",
    "BNB": "bnbusdt",
}

ASSETS = list(ASSET_SYMBOLS.keys())

# Live testing caps — ultra-small while validating new LIMIT exits and fee logic
MAX_EXPOSURE_PER_ASSET_USD = (
    2.00  # Hard cap: total contracts value per asset <= $2 (raised from $1)
)
MAX_GLOBAL_EXPOSURE_USD = 10.00  # Global hard cap across all assets combined (raised to support 5 concurrent x $2 per asset)
MAX_POSITION_CONTRACTS = 1  # Allow only 1 contract per position
MAX_POSITIONS_PER_ASSET = 1  # Only one active position per asset at a time
MIN_CONTRACT_PRICE = 0.08  # Avoid extremely cheap penny options (lowered from 0.15 to allow cheap NO bets)

# Maximum contracts per single trade
MAX_CONTRACTS_PER_TRADE = 10  # Maximum 10 contracts per single trade

NO_ENTRY_LAST_SECONDS = 30  # 30s lockout — lowered from 120s to allow CFB RTI settlement edge in final minute

MIN_MULTIPLIER = (
    1.00  # Near-expiry binary markets have strike ~= spot, so multiplier ~1.0
)
MAX_MULTIPLIER = 100.0

# Asset-specific thresholds (high-cap vs altcoin volatility tiers)
ASSET_TIERS = {
    "HIGH_CAP": ["BTC", "ETH"],
    "ALTCOIN": ["SOL", "DOGE", "XRP", "HYPE", "BNB"],
}

TIER_PARAMS = {
    "HIGH_CAP": {
        "IMPULSE_THRESHOLD_PCT": 0.00015,  # 0.015% - lowered from 0.025% to catch smaller moves
        "STRIKE_PROXIMITY_PCT": 0.0015,  # 0.15% - must be very close to the strike to win
    },
    "ALTCOIN": {
        "IMPULSE_THRESHOLD_PCT": 0.0002,  # 0.02% - lowered from 0.04% to catch smaller altcoin swings
        "STRIKE_PROXIMITY_PCT": 0.003,  # 0.3% - wider strike allowance for altcoins
    },
}

# Asset-specific stop-loss overrides (None = use global STOP_LOSS_PCT)
# All assets now use the global STOP_LOSS_PCT with time-tightening
ASSET_STOP_LOSS_OVERRIDE = {}

# Default fallback (used if asset not in tier map)
IMPULSE_THRESHOLD_PCT = 0.0004
STRIKE_PROXIMITY_PCT = 0.015

# Expected Value (EV) Entry Mode
USE_EV_ENTRY = True
MIN_EV_EDGE = 0.03  # Require at least 3% positive EV edge per trade (was 0.05)

# Stage 2: Feature Flags
ENABLE_EXECUTION_GUARDS = True
ENABLE_ML_SIZING = True
ENABLE_NEW_EXITS = True
ENABLE_MAKER_REGIME = True
ENABLE_SNIPER_REGIME = True

# IWMC Testing Mode
IWMC_ONLY_MODE = True  # When True, ONLY IWMC signals are used (disables panic_fade, gap_fade, ML, EV, etc.)

# CRMD (CFB RTI Momentum Divergence) Testing Mode
# Fires in final 180s when RTI momentum diverges from Kalshi price. Independent of IWMC_ONLY_MODE.
ENABLE_CRMD = True
# CRMD parameters
CRMD_MOMENTUM_LOOKBACK = 180.0  # seconds to look back for RTI momentum
CRMD_MOMENTUM_THRESHOLD_BPS = 0.5  # minimum bps/sec to trigger
CRMD_PRICE_DIVERGENCE_PCT = 0.03  # 3% divergence required (avoids single-tick noise)
CRMD_SIZING_MULTIPLIER = 2.0  # large conviction sizing
CRMD_SIGNAL_COOLDOWN = 30  # seconds between CRMD signals on same asset

# SKEW_FADE (Orderbook Skew Fade) Testing Mode
# Fades extreme orderbook imbalances in mid-window. Independent of IWMC_ONLY_MODE.
ENABLE_SKEW_FADE = True  # Enabled — orderbook skew fade strategy
# SKEW_FADE parameters
SKEW_THRESHOLD = 0.75  # minimum skew ratio (75% on one side) to trigger
SKEW_MIN_TOTAL_DEPTH = 10  # minimum total book depth for valid signal
SKEW_SIGNAL_COOLDOWN = 45  # seconds between SKEW_FADE signals on same asset
SKEW_FADE_CONFIDENCE_THRESHOLD = 0.5  # minimum confidence to override other signals

# Coinbase Advanced Trade API
USE_COINBASE_ADVANCED = (
    True  # Use Coinbase Advanced Trade API V3 instead of legacy public feed
)


# Stage 3B: Execution Guards
GUARD_MARKET_COOLDOWN_SEC = 30
GUARD_REQUOTE_COOLDOWN_SEC = 5
GUARD_MAX_SPREAD_PCT = 0.05
GUARD_MIN_BOOK_DEPTH = 5

# Stage 4: Risk Caps ($50 account, live testing with 1-contract safety cap)
MAX_CONTRACTS_PER_TRADE_CAP = 1  # 1 contract max while validating new exit logic
MAX_DOLLARS_PER_TRADE_CAP = 1.00  # $1 max per trade
MAX_OPEN_CONTRACTS_PER_MARKET_CAP = 1
MAX_DAILY_LOSS_CAP = 3.00  # $3 daily loss cap (6% of $50 account)
MAX_CONCURRENT_POSITIONS = 5  # Allow up to 5 concurrent positions (one per asset)

# ML Standalone Veto Filter
USE_ML_VETO = True
ML_CONFIDENCE_THRESHOLD = 0.55  # Lowered from 0.60 to allow more signals through

# Fee-aware trading: estimated round-trip fee per contract (entry + exit taker fees)
# Kalshi taker fees are roughly $0.01-$0.016 per contract per side
ESTIMATED_ROUND_TRIP_FEE_PER_CONTRACT = 0.03
MIN_EV_AFTER_FEES = 0.05  # Only trade if expected value after fees is at least 5 cents

# Post-stop cooldown: seconds to wait after a stop-loss before re-entering same asset
STOP_LOSS_COOLDOWN_SECONDS = 90

# Post-trade cooldown: seconds to wait after ANY trade exit before re-entering
POST_TRADE_COOLDOWN_SECONDS = 120

# Minimum hold time before hard/trailing stops can trigger (prevents instant catastrophic exits on thin books)
# End-of-window exits are exempt — they always fire
MIN_HOLD_BEFORE_STOP_SECONDS = 60  # Raised from 30 to give trades time to breathe

# Minimum expected gross profit required to justify a trade
MIN_EXPECTED_GROSS_PROFIT_USD = 0.06

# Debug: log raw ticker data for first N ticks to diagnose field names
LOG_RAW_TICKER_KEYS = False  # Toggle on to see actual Kalshi WS field names

# Spread filter: skip entry if bid-ask spread exceeds this % of mid price
MAX_SPREAD_PCT = 0.04  # 4% max spread to avoid getting crushed by slippage

# Order Book Imbalance Threshold
MIN_BOOK_IMBALANCE = (
    0.55  # Require 55% order book pressure in our direction (loosened from 60%)
)

# Exit Parameters (15-min binary options)
TAKE_PROFIT_PCT = 0.75  # Take profit at 75% gain (was 3.00)
STOP_LOSS_PCT = -0.25  # Tightened from -35% to -25% to cut losers faster
PROFIT_PROTECTION_TRIGGER = 0.75  # (Legacy)
DISABLE_EARLY_EXITS = False  # Enable early stop-loss, salvage exits, and take-profit
MIN_HOLD_TIME_SECONDS = 90  # 90s hold timer before take-profit placement (hard/trailing stops fire immediately)
TRAILING_STOP_PCT = (
    0.25  # Trail at 25% drawdown from peak, activate after 20% peak (was 0.12/0.15)
)

# Exit Retry Parameters
MAX_EXIT_LIMIT_RETRIES = 3  # Max LIMIT order retries before forcing IoC MARKET exit
EXIT_RETRY_COOLDOWN_SECONDS = 5  # Cooldown between exit attempts

# IoC Fill Validation Parameters
IOC_MIN_FILL_RETRY = 2  # Max retries for IoC orders that return 0 fill
IOC_RETRY_PRICE_BUFFER = 0.01  # Price buffer ($0.01) for IoC retries

# Option Pricing Parameters
MIN_EDGE = 0.02  # Lowered from 0.05 to allow more trades
VOLATILITY_WINDOW_SECONDS = 600  # 10-minute window for rolling volatility
PRICE_SAMPLE_INTERVAL = 1.0  # Throttle tick storage to 1 sample per second

KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

# Sandbox mode: use demo.kalshi.co sandbox API instead of live production
# FALSE = live production trading with real money
SANDBOX_MODE = False
KALSHI_DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"

# -------------------------------------------------------------------
# Entry Filter Parameters
# -------------------------------------------------------------------
# Maximum distance from the contract threshold in basis points (1bp = 0.01%)
# Skip entry if abs(spot - threshold) / spot * 10000 > this value
MAX_DISTANCE_BPS = 20

# Momentum lookback window in minutes (how far back to measure price momentum)
MOMENTUM_LOOKBACK_MINUTES = 3

# Minimum absolute momentum in basis points to confirm a signal direction
MIN_MOMENTUM_ABS_BPS = 8  # Lowered from 15 to catch smaller trends

# Max number of times the bot will re-enter the same 15-minute contract
MAX_REENTRIES_PER_CONTRACT = 0  # 0 = one shot per window; prevents stop-loss churn

# Max total contracts across all entries in the same 15-minute window
MAX_CONTRACTS_PER_15M_WINDOW = 10

# Max total contracts open for a single asset across all windows
MAX_CONTRACTS_PER_ASSET = 20

# Seconds to wait after a fill before entering the same asset again
COOLDOWN_SECONDS_AFTER_FILL = 120

# Side logic mode: 'velocity_momentum' (Coinbase 1-min velocity as primary signal)
# velocity_momentum: buy YES when Coinbase 1-min velocity > threshold; NO side converted to sell YES
# continuation:   buy YES when spot > threshold and rising; buy NO when spot < threshold and falling
SIDE_LOGIC_MODE = "velocity_momentum"

# Multi-timeframe trend confirmation
# Require price trend at multiple lookbacks to confirm signal direction
ENABLE_MULTI_TF_CONFIRMATION = True
# Lookback windows in seconds for trend confirmation checks
TREND_CONFIRMATION_LOOKBACKS = [60, 300, 600]  # 1min, 5min, 10min
# Minimum momentum (bps) required at each TF lookback to confirm direction
TF_CONFIRMATION_MIN_BPS = 3  # 3 bps minimum at each timeframe

# Early-window aggression (time_remaining > EARLY_WINDOW_BPS_REMAINING)
# Relax thresholds in the first ~5 minutes of the 15-min window to catch
# signals before the market has established a clear directional trend
EARLY_WINDOW_BPS_REMAINING = 600  # Relax gates when >600s remain (first ~5min)
EARLY_IMPULSE_MULTIPLIER = 0.5  # Halve impulse threshold early (more sensitive)
EARLY_SPREAD_MULTIPLIER = 2.0  # Double spread allowance early (8% vs 4%)
EARLY_LOW_TRUE_MULTIPLIER = 1.5  # Accept 1.5x multiplier early (vs 2.0x)
EARLY_SKIP_REQUIRES_STRONG_MOVE = True  # Skip redundant strong-move check early
EARLY_MIN_CONTRACT_PRICE = 0.05  # Accept cheaper contracts early (vs 0.08)

# Kalshi V2 expresses all orders as YES bid/ask:
#   ENTER_YES (LONG) = buy YES (bid), ENTER_NO (SHORT) = sell YES (ask)

# Standard Kalshi Taker fee per contract (e.g., 1.5 cents)
TAKER_FEE_PER_CONTRACT = 0.015

# -------------------------------------------------------------------
# CF Benchmarks RTI via Kalshi WS
# -------------------------------------------------------------------
ENABLE_CFB_RTI = True  # Subscribe to CF Benchmarks RTI value feed

# CF Benchmarks Real-Time Index IDs for each Kalshi asset
# Used for settlement price prediction in the final 60 seconds of each 15-min window
CFB_INDEX_MAP = {
    "BTC": "BRTI",
    "ETH": "ETHUSD_RTI",
    "SOL": "SOLUSD_RTI",
    "XRP": "XRPUSD_RTI",
    "DOGE": "DOGEUSD_RTI",
    # HYPE and BNB — verify if CFB indices exist; fall back to Coinbase-only for now
}

# Coinbase 1-minute velocity thresholds (in basis points)
# Velocity momentum: buy YES when Coinbase 1-min velocity exceeds this threshold
# BTC/ETH: 2 bps (0.02%) — tighter for liquid pairs
# Alts: 4 bps (0.04%) — wider to avoid noise
COINBASE_VELOCITY_THRESHOLD_BPS = {
    "HIGH_CAP": 2.0,
    "ALTCOIN": 4.0,
}

# -------------------------------------------------------------------
# Trade Frequency via Binance @aggTrade
# -------------------------------------------------------------------
ENABLE_TRADE_FREQ = True  # Track Binance @aggTrade rate as confirmation

# Spot symbol mapping for each Kalshi asset
# Used to look up the correct Binance/Coinbase feed symbol
SPOT_SYMBOL_MAP = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
    "DOGE": "dogeusdt",
    "HYPE": "hypeusdt",
    "XLM": "xlmusdt",
    "BNB": "bnbusdt",
}

# Coinbase Pro symbol mapping for each Kalshi asset
# Used by CoinbaseClient for WS ticker subscription
COINBASE_SYMBOL_MAP = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "HYPE": "HYPE-USD",
    "BNB": "BNB-USD",
}

# -------------------------------------------------------------------
# Google Sheets write-rate throttling
# -------------------------------------------------------------------
# Seconds to sleep after each Google Sheets update to avoid 429 quota errors
SHEET_WRITE_DELAY = 1.0

# Position Sizing Constants for Kelly Sizer ($50 account)
PORTFOLIO_RISK_FRACTION = (
    0.10  # Risk 10% of portfolio per trade ($4.30 at $43, up from 5%)
)
MIN_POSITION_USD = 0.01  # Minimum position size in USD
MAX_POSITION_USD = 10.00  # Maximum position size in USD (per trade)
MIN_ACCOUNT_BALANCE = (
    5.00  # Minimum account balance required to open new positions ($50 * 10%)
)


def _validate():
    errors = []
    if not ASSET_SYMBOLS:
        errors.append("ASSET_SYMBOLS is empty — at least one asset required")
    if MAX_EXPOSURE_PER_ASSET_USD <= 0:
        errors.append(
            f"MAX_EXPOSURE_PER_ASSET_USD must be > 0, got {MAX_EXPOSURE_PER_ASSET_USD}"
        )
    if MAX_GLOBAL_EXPOSURE_USD <= 0:
        errors.append(
            f"MAX_GLOBAL_EXPOSURE_USD must be > 0, got {MAX_GLOBAL_EXPOSURE_USD}"
        )
    if MAX_DAILY_LOSS_CAP <= 0:
        errors.append(f"MAX_DAILY_LOSS_CAP must be > 0, got {MAX_DAILY_LOSS_CAP}")
    if MIN_ACCOUNT_BALANCE <= 0:
        errors.append(f"MIN_ACCOUNT_BALANCE must be > 0, got {MIN_ACCOUNT_BALANCE}")
    if not KALSHI_BASE_URL.startswith("https://"):
        errors.append(
            f"KALSHI_BASE_URL must start with https://, got {KALSHI_BASE_URL}"
        )
    for asset in ASSET_SYMBOLS:
        if asset not in COINBASE_SYMBOL_MAP:
            errors.append(
                f"Asset {asset} has no Coinbase symbol mapping in COINBASE_SYMBOL_MAP"
            )
    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))


_validate()
