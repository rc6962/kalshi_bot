# Spread & Tier Parameter Analysis

## Critical Findings

### 1. **Tier Parameters Defined But NOT Used**

In `config.py`:

```python
ASSET_TIERS = {
    "HIGH_CAP": ["BTC", "ETH"],
    "ALTCOIN": ["SOL", "DOGE", "XRP", "HYPE"]
}

TIER_PARAMS = {
    "HIGH_CAP": {
        "IMPULSE_THRESHOLD_PCT": 0.0003,   # 0.03%
        "STRIKE_PROXIMITY_PCT": 0.08,      # 8%
    },
    "ALTCOIN": {
        "IMPULSE_THRESHOLD_PCT": 0.0005,   # 0.05%
        "STRIKE_PROXIMITY_PCT": 0.12,      # 12%
    }
}
```

But in `signal_engine.py` and `event_loop.py`:

- Only the fallback defaults are used: `IMPULSE_THRESHOLD_PCT = 0.0003`, `STRIKE_PROXIMITY_PCT = 0.10`
- `ASSET_TIERS` and `TIER_PARAMS` are **never imported or referenced**

### 2. **Spread Filter Defined But NOT Implemented**

In `config.py`:

```python
MAX_SPREAD_PCT = 0.08  # 8%
```

But no spread calculation or filtering logic exists in:

- `signal_engine.py` - No spread check in `evaluate()`
- `event_loop.py` - Has access to `yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars` but doesn't compute spread

### 3. **Available Ticker Data for Spread Calculation**

From `event_loop.py` handle_ticker():

```python
yes_price = normalize_price(
    ticker_data.get("yes_price")
    or ticker_data.get("yes_price_dollars")
    or ticker_data.get("yes_ask_dollars")   # <-- ASK available
    or ticker_data.get("yes_bid_dollars")   # <-- BID available
)
no_price = normalize_price(
    ticker_data.get("no_price")
    or ticker_data.get("no_price_dollars")
    or ticker_data.get("no_ask_dollars")    # <-- ASK available
    or ticker_data.get("no_bid_dollars")    # <-- BID available
)
```

Both bid and ask prices are available for YES and NO sides.

---

## Required Implementations

### A. Add Tier-Aware Signal Engine

```python
# In signal_engine.py - modify evaluate() to accept asset parameter
def evaluate(self, contract_price, strike, spot_price,
             multiplier, time_remaining,
             recent_move_pct, futures_trend=0.0, asset="BTC"):

    # Get tier-specific params
    from config import ASSET_TIERS, TIER_PARAMS, IMPULSE_THRESHOLD_PCT, STRIKE_PROXIMITY_PCT

    # Determine asset tier
    tier = "HIGH_CAP" if asset in ASSET_TIERS.get("HIGH_CAP", []) else "ALTCOIN"
    params = TIER_PARAMS.get(tier, {})

    impulse_threshold = params.get("IMPULSE_THRESHOLD_PCT", IMPULSE_THRESHOLD_PCT)
    strike_proximity = params.get("STRIKE_PROXIMITY_PCT", STRIKE_PROXIMITY_PCT)

    # Use tier-specific thresholds...
```

### B. Add Spread Calculation & Filtering

```python
# In event_loop.py - add spread calculation in handle_ticker()
def calculate_spread(ticker_data, side):
    """Calculate bid-ask spread as % of mid price"""
    if side == "yes":
        bid = ticker_data.get("yes_bid_dollars")
        ask = ticker_data.get("yes_ask_dollars")
    else:
        bid = ticker_data.get("no_bid_dollars")
        ask = ticker_data.get("no_ask_dollars")

    if bid is None or ask is None:
        return None

    bid = float(bid)
    ask = float(ask)
    if bid > 100: bid /= 100
    if ask > 100: ask /= 100

    mid = (bid + ask) / 2
    if mid == 0:
        return None

    spread_pct = (ask - bid) / mid
    return spread_pct

# In entry logic - filter by MAX_SPREAD_PCT
from config import MAX_SPREAD_PCT
spread = calculate_spread(ticker_data, side)
if spread is not None and spread > MAX_SPREAD_PCT:
    print(f"Spread too wide: {spread:.2%} > {MAX_SPREAD_PCT:.2%}")
    return  # Skip entry
```

### C. Pass Asset to Signal Engine

```python
# In event_loop.py handle_ticker() - pass self.asset
signal = self.signal.evaluate(
    contract_price=contract_price,
    strike=self.strike,
    spot_price=spot,
    multiplier=multiplier,
    time_remaining=time_remaining,
    recent_move_pct=move_pct,
    futures_trend=futures_trend,
    asset=self.asset  # ADD THIS
)
```

---

## Impact on Profitability

| Issue                              | Current State                  | Fix Impact                                                                |
| ---------------------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| No tier params                     | All assets use 0.03% threshold | HIGH_CAP: tighter entries (0.03%), ALTCOIN: wider (0.05%) - reduces noise |
| No spread filter                   | Entering during wide spreads   | Avoid negative edge trades - critical for binary options                  |
| No asset-specific strike proximity | All use 10%                    | HIGH_CAP: 8% (better liquidity), ALTCOIN: 12% (more vol)                  |

---

## Spread Edge Analysis for Binary Options

For Kalshi binary options:

- **Spread = direct cost** (you buy at ask, sell at bid)
- **Edge must exceed spread** to be profitable
- Example: Contract at 0.40 bid / 0.45 ask = 11% spread
  - Buying YES at 0.45, need >55% win prob to break even
  - Current model ignores this!

---

## Priority Implementation Order

1. **HIGH** - Add spread calculation and MAX_SPREAD_PCT filter in entry logic
2. **HIGH** - Implement tier-specific impulse thresholds in SignalEngine
3. **HIGH** - Implement tier-specific strike proximity in SignalEngine
4. **MEDIUM** - Add asset parameter to SignalEngine.evaluate()
5. **MEDIUM** - Log spread metrics for monitoring
