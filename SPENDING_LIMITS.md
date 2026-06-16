# Spending Limits Configuration

## Current Limits (Updated: 2026-06-11)

### Per-Trade Limits

```python
MAX_POSITION_USD = 2.00        # Maximum $2 total per open position
MAX_POSITION_CONTRACTS = 4     # Maximum 4 contracts per trade
MAX_POSITIONS_PER_ASSET = 1    # Only 1 active position per asset
```

### Price Limit

- **Maximum contract price**: $0.50 per contract
- Any contract priced above $0.50 will be automatically rejected

### What This Means:

- **Maximum risk per trade**: $2 total (e.g., 4 contracts at $0.50 each)
- **Maximum contracts**: 4 contracts total
- **Concurrent positions**: Only 1 open trade per cryptocurrency at a time

### Example Calculations:

- If contract price = $0.50 → max 4 contracts ($2 total)
- If contract price = $0.25 → max 4 contracts (capped by MAX_POSITION_CONTRACTS)
- If contract price = $0.10 → max 4 contracts (capped by MAX_POSITION_CONTRACTS)
- If contract price = $0.60 → TRADE REJECTED (exceeds $0.50 limit)

### Daily Spending:

The bot has **no explicit daily or total spending limit** beyond these per-trade caps. It will continue trading as long as signals are generated and your account balance allows.

## How to Adjust Limits

### Option 1: Edit config.py directly

Open `c:\Tradingbots\kalshi_bot\config.py` and modify these lines:

```python
MAX_POSITION_USD = 2.00   # Change this value
MAX_POSITION_CONTRACTS = 4   # Change this value
```

### Option 2: Let me adjust them for you

Just tell me what limits you want, and I'll update the configuration.

### Recommended Limits for Different Phases:

**Conservative (Initial Testing):**

```python
MAX_POSITION_USD = 2.00
MAX_POSITION_CONTRACTS = 4
```

**Moderate (After 50+ winning trades):**

```python
MAX_POSITION_USD = 5.00
MAX_POSITION_CONTRACTS = 10
```

**Aggressive (After 200+ trades with proven edge):**

```python
MAX_POSITION_USD = 10.00
MAX_POSITION_CONTRACTS = 20
```

## Important Notes

1. **Price Limit**: The $0.50 per contract limit is enforced in `signal_engine.py` and cannot be changed via config.py without code modification.

2. **Kelly Criterion**: The bot uses Kelly Criterion position sizing which will automatically reduce position sizes during losing streaks and increase during winning streaks, but will never exceed `MAX_POSITION_USD` or `MAX_POSITION_CONTRACTS`.

3. **Account Balance**: The bot does not check your total account balance before trading. Ensure you have sufficient funds in your Kalshi account.

4. **Risk Warning**: These limits are designed to minimize risk during initial data collection. Increase them only after validating the strategy's profitability.

---

**To increase limits later, simply ask me to update the configuration.**
