# Implementation Summary - Kalshi Bot Fixes & Improvements

## Date: 2026-06-11

## Changes Made

### 1. Entry Filter Relaxations (To Enable Trading & Data Collection)

**config.py changes:**

- `MAX_POSITION_USD`: 1.00 → 10.00 (10x increase)
- `MAX_POSITION_CONTRACTS`: 5 → 50 (10x increase)
- `NO_ENTRY_LAST_SECONDS`: 180 → 60 (allows trading closer to expiry)
- `MIN_MULTIPLIER`: 2.5 → 2.0 (allows more trades)
- `MAX_SPREAD_PCT`: 8% → 20% (accepts wider spreads temporarily)
- `MIN_BOOK_IMBALANCE`: 0.65 → 0.45 (reduced liquidity requirement)
- `STRIKE_PROXIMITY_PCT` (HIGH_CAP): 8% → 15%
- `STRIKE_PROXIMITY_PCT` (ALTCOIN): 12% → 25%

**signal_engine.py changes:**

- Disabled time-of-day filter (2-6 AM UTC block removed)
- Disabled futures trend alignment filter (allows all trades)
- Added detailed rejection logging with specific reasons

### 2. Improved Profitability Prediction

**New file: `ml/expected_value_model.py`**

- Trains regression model to predict expected profit percentage
- Features added: implied volatility, bid-ask ratio, volume velocity
- Output: expected profit per contract (e.g., 0.03 = 3% expected return)
- Usage: Only trade if expected_value > MIN_EXPECTED_VALUE (e.g., 0.02)

**Benefits over binary classification:**

- Provides magnitude of expected profit, not just win/loss
- Better for position sizing decisions
- More stable with smaller datasets

### 3. Kelly Criterion Position Sizing

**New file: `engine/kelly_sizer.py`**

- Implements optimal position sizing using Kelly formula
- Dynamically adjusts based on win rate and profit/loss ratios
- Uses Half-Kelly (50% of full Kelly) for safety
- Automatically updates capital after each trade

**Modified `engine/risk_manager.py`:**

- Integrated KellyPositionSizer
- Replaced simple USD-based sizing with Kelly optimization
- Added win_prob parameter for ML predictions
- Tracks historical performance for adaptive sizing

### 4. Enhanced Logging & Monitoring

**signal_engine.py logging additions:**

- [SignalEngine] REJECTED: time_remaining
- [SignalEngine] REJECTED: multiplier outside range
- [SignalEngine] REJECTED: spread_pct threshold
- [SignalEngine] REJECTED: strike_distance threshold
- [SignalEngine] REJECTED: imbalance threshold
- [SignalEngine] Raw signal: ENTER_YES/ENTER_NO

## Next Steps

### Immediate (Next 24-48 hours):

1. Run the bot and monitor logs to see if trades are being executed
2. Check `trades.csv` for first filled orders
3. Observe which rejection reasons appear most frequently
4. If still no trades, consider further loosening filters

### Short-term (This week):

1. Accumulate at least 50-100 real trades
2. Run `python ml/expected_value_model.py --data trades.csv` to train EV model
3. Integrate EV model into signal_engine.py (uncomment ML gate section)
4. Monitor performance metrics (win rate, avg profit, Sharpe ratio)

### Medium-term (Next 1-2 weeks):

1. Gradually tighten filters based on data (e.g., reduce MAX_SPREAD_PCT to 15%)
2. Re-enable futures trend alignment filter with calibrated thresholds
3. Implement ensemble model (combine rule-based + ML + EV)
4. Add cross-asset correlation features

### Long-term (Month+):

1. Deploy to cloud for 24/7 operation
2. Implement backtesting framework for strategy optimization
3. Add real-time volatility surface modeling
4. Explore reinforcement learning for dynamic threshold adjustment

## Risk Warning

⚠️ **Important**: These changes significantly increase trading frequency and position sizes to collect data. Monitor closely:

- Ensure you have sufficient account balance
- Set daily loss limits in configuration
- Review all trades daily during initial collection phase
- Consider running in simulation mode first if available

## Configuration Recommendations

After collecting 100+ trades, consider these tighter settings:

```python
MAX_SPREAD_PCT = 0.12          # 12% (stricter)
MIN_BOOK_IMBALANCE = 0.55      # 55% (stricter)
NO_ENTRY_LAST_SECONDS = 120    # 2 minutes
STRIKE_PROXIMITY_PCT = 0.18    # 18% for alts
```

## Success Metrics

Target performance after optimization:

- Win rate: > 55%
- Average profit per trade: > 2%
- Sharpe ratio: > 1.5
- Maximum drawdown: < 15%

---

**The bot should now start trading and collecting real market data.**

Monitor `trades.csv` and console logs for execution confirmation.
