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

# Fixed $5 max exposure per asset (user requirement)
# The bot will NEVER hold more than $5 worth of contracts per asset
# It can scale into a position with multiple smaller entries but total <= $5
MAX_EXPOSURE_PER_ASSET_USD = 5.00    # Hard cap: total contracts value per asset <= $5
MAX_GLOBAL_EXPOSURE_USD = 10.00       # Global hard cap across all assets combined
MAX_POSITION_CONTRACTS = 10           # Allow up to 10 contracts (at $0.50 each = $5 max)
MAX_POSITIONS_PER_ASSET = 1           # Only one active position per asset at a time
MIN_CONTRACT_PRICE = 0.15             # Avoid extremely cheap penny options (minimum 15 cents)

# Maximum contracts per single trade (based on $5 max / minimum contract price $0.01)
MAX_CONTRACTS_PER_TRADE = 5          # Maximum 5 contracts per single trade to stay under $5 limit

NO_ENTRY_LAST_SECONDS = 300  # Set to 5 minutes to prevent opening trades right before expiry

MIN_MULTIPLIER = 1.00  # Near-expiry binary markets have strike ~= spot, so multiplier ~1.0
MAX_MULTIPLIER = 100.0

# Asset-specific thresholds (high-cap vs altcoin volatility tiers)
ASSET_TIERS = {
    "HIGH_CAP": ["BTC", "ETH"],
    "ALTCOIN": ["SOL", "DOGE", "XRP", "HYPE", "BNB"]
}

TIER_PARAMS = {
    "HIGH_CAP": {
        "IMPULSE_THRESHOLD_PCT": 0.00025,  # 0.025% - BTC/ETH typical 15m moves are 0.02-0.04%
        "STRIKE_PROXIMITY_PCT": 0.0015,   # 0.15% - must be very close to the strike to win
    },
    "ALTCOIN": {
        "IMPULSE_THRESHOLD_PCT": 0.0004,   # 0.04% - altcoins are more volatile but still need a trigger
        "STRIKE_PROXIMITY_PCT": 0.003,    # 0.3% - wider strike allowance for altcoins
    }
}

# Default fallback (used if asset not in tier map)
IMPULSE_THRESHOLD_PCT = 0.0004
STRIKE_PROXIMITY_PCT = 0.015

# Expected Value (EV) Entry Mode
# Disabled because the ML model accuracy is currently too low (47%). We rely on strict technical rules.
USE_EV_ENTRY = False
MIN_EV_EDGE = 0.05   # Require at least 5% positive EV edge per trade (if enabled)

# ML Standalone Veto Filter
USE_ML_VETO = True
ML_CONFIDENCE_THRESHOLD = 0.50

# Debug: log raw ticker data for first N ticks to diagnose field names
LOG_RAW_TICKER_KEYS = True  # Toggle on to see actual Kalshi WS field names

# Spread filter: skip entry if bid-ask spread exceeds this % of mid price
MAX_SPREAD_PCT = 0.04  # 4% max spread to avoid getting crushed by slippage

# Order Book Imbalance Threshold
MIN_BOOK_IMBALANCE = 0.55  # Require 55% order book pressure in our direction (loosened from 60%)

TAKE_PROFIT_PCT = 1.00            # Take profit immediately at 100% gain
STOP_LOSS_PCT = -0.50             # Tightened to -0.50 to prevent catastrophic bleed
PROFIT_PROTECTION_TRIGGER = 0.75  # (Legacy) Take profit at +75% gain (exit unless momentum favors holding)
DISABLE_EARLY_EXITS = False       # Set to False to enable early stop-loss, salvage exits, and take-profit
MIN_HOLD_TIME_SECONDS = 120       # Give mean-reversion trades 120 seconds to breathe before checking momentum

# Option Pricing Parameters
MIN_EDGE = 0.02                   # Lowered from 0.05 to allow more trades
VOLATILITY_WINDOW_SECONDS = 600   # 10-minute window for rolling volatility
PRICE_SAMPLE_INTERVAL = 1.0       # Throttle tick storage to 1 sample per second

KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

# -------------------------------------------------------------------
# Entry Filter Parameters
# -------------------------------------------------------------------
# Maximum distance from the contract threshold in basis points (1bp = 0.01%)
# Skip entry if abs(spot - threshold) / spot * 10000 > this value
MAX_DISTANCE_BPS = 20

# Momentum lookback window in minutes (how far back to measure price momentum)
MOMENTUM_LOOKBACK_MINUTES = 3

# Minimum absolute momentum in basis points to confirm a signal direction
MIN_MOMENTUM_ABS_BPS = 15

# Max number of times the bot will re-enter the same 15-minute contract
MAX_REENTRIES_PER_CONTRACT = 1

# Max total contracts across all entries in the same 15-minute window
MAX_CONTRACTS_PER_15M_WINDOW = 10

# Max total contracts open for a single asset across all windows
MAX_CONTRACTS_PER_ASSET = 20

# Seconds to wait after a fill before entering the same asset again
COOLDOWN_SECONDS_AFTER_FILL = 120

# Side logic mode: 'continuation' or 'mean_reversion'
# continuation:   buy YES when spot > threshold and rising; buy NO when spot < threshold and falling
# mean_reversion: buy NO when spot > threshold and rising; buy YES when spot < threshold and falling
SIDE_LOGIC_MODE = "continuation"

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

# -------------------------------------------------------------------
# Google Sheets write-rate throttling
# -------------------------------------------------------------------
# Seconds to sleep after each Google Sheets update to avoid 429 quota errors
SHEET_WRITE_DELAY = 1.0

# Position Sizing Constants for Kelly Sizer
PORTFOLIO_RISK_FRACTION = 0.02    # Risk 2% of portfolio per trade (when not using Kelly)
MIN_POSITION_USD = 0.01           # Minimum position size in USD
MAX_POSITION_USD = 5.00           # Maximum position size in USD (per trade)
MIN_ACCOUNT_BALANCE = 0.50        # Minimum account balance required to open new positions (USD)