"""
Backtester to generate ML training data for Kalshi mean-reversion strategy.
Replays historical market data through SignalEngine and logs features + outcomes.
"""
import csv
import os
import random
from datetime import datetime, timedelta
from engine.signal_engine import SignalEngine
from config import (
    IMPULSE_THRESHOLD_PCT, MIN_MULTIPLIER, MAX_MULTIPLIER,
    STRIKE_PROXIMITY_PCT, NO_ENTRY_LAST_SECONDS, STOP_LOSS_PCT,
    PROFIT_PROTECTION_TRIGGER, ASSET_TIERS, TIER_PARAMS,
    MAX_SPREAD_PCT
)

# Output file for ML training data
ML_DATA_PATH = "ml_training_data.csv"

# Features to log for each trade opportunity
FEATURE_COLUMNS = [
    "timestamp", "asset", "multiplier", "strike_distance_pct",
    "recent_move_pct", "time_remaining_sec", "futures_trend", "spread_pct",
    "contract_price", "spot_price", "strike_price",
    "side", "entry_price", "exit_price", "pnl_pct", "outcome"
]


def generate_synthetic_market_data(num_samples=1000, asset="BTC"):
    """
    Generate synthetic market data that mimics Kalshi 15-min binary options.
    Replace this with real historical data when available.
    """
    data = []
    base_spot = 65000.0 if asset == "BTC" else 3000.0
    spot = base_spot
    
    for i in range(num_samples):
        # Simulate time remaining in 15-min window (900 seconds)
        time_remaining = random.randint(30, 850)
        
        # Random walk for spot price
        move_pct = random.gauss(0, 0.002)  # 0.2% std dev
        spot *= (1 + move_pct)
        
        # Strike near current spot (within 15%)
        strike_offset = random.uniform(-0.10, 0.10)
        strike = spot * (1 + strike_offset)
        
        # Contract price based on moneyness and time
        moneyness = (spot - strike) / strike
        base_prob = 0.5 + moneyness * 2  # Simplified pricing
        contract_price = max(0.05, min(0.95, base_prob + random.gauss(0, 0.05)))
        
        # Simulate spread (typically 0.01 to 0.05 on Kalshi)
        spread = random.uniform(0.01, 0.04)
        bid = max(0.01, contract_price - (spread / 2))
        ask = min(0.99, contract_price + (spread / 2))
        
        # Multiplier = potential payout / cost
        multiplier = 1.0 / contract_price if contract_price > 0 else 100.0
        
        # Recent move (last few ticks)
        recent_move = random.gauss(0, 0.0005)
        
        # Futures trend (correlated with spot but noisy)
        futures_trend = move_pct * 0.8 + random.gauss(0, 0.0003)
        
        # Simulate outcome: did mean reversion happen?
        # Higher probability if recent move was extreme
        reversion_prob = 0.5 + abs(recent_move) * 100
        reversion_prob = min(0.8, max(0.2, reversion_prob))
        
        if recent_move < 0:
            # Bet YES (expect bounce up)
            side = "yes"
            won = random.random() < reversion_prob
        else:
            # Bet NO (expect reversal down)
            side = "no"
            won = random.random() < reversion_prob
        
        # Simulate PnL
        entry_price = contract_price
        if won:
            exit_price = 1.0 if side == "yes" else 0.0
        else:
            exit_price = 0.0 if side == "yes" else 1.0
        
        pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
        
        data.append({
            "timestamp": datetime.utcnow().isoformat(),
            "asset": asset,
            "multiplier": round(multiplier, 2),
            "strike_distance_pct": round(abs(spot - strike) / strike, 4),
            "recent_move_pct": round(recent_move, 6),
            "time_remaining_sec": time_remaining,
            "futures_trend": round(futures_trend, 6),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread_pct": round((ask - bid) / ((ask + bid) / 2), 4),
            "contract_price": round(contract_price, 4),
            "spot_price": round(spot, 2),
            "strike_price": round(strike, 2),
            "side": side,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "pnl_pct": round(pnl_pct, 4),
            "outcome": 1 if pnl_pct > 0 else 0
        })
    
    return data


def run_backtest(data):
    """
    Replay data through SignalEngine and filter only valid signals.
    Returns list of trades that passed all entry filters.
    """
    signal_engine = SignalEngine()
    valid_trades = []
    
    for row in data:
        # Replay using the actual SignalEngine logic to ensure training data matches production
        signal = signal_engine.evaluate(
            asset_name=row.get("asset"),
            bid=row.get("bid"),
            ask=row.get("ask"),
            strike=row.get("strike_price"),
            spot_price=row.get("spot_price"),
            multiplier=row.get("multiplier"),
            time_remaining=row.get("time_remaining_sec"),
            recent_move_pct=row.get("recent_move_pct"),
            futures_trend=row.get("futures_trend")
        )
        
        if not signal:
            continue
        
        # Only include if simulated side matches signal
        expected_side = "yes" if signal == "ENTER_YES" else "no"
        if row["side"] != expected_side:
            continue
        
        valid_trades.append(row)
    
    return valid_trades


def save_training_data(trades, output_path=ML_DATA_PATH):
    """Save filtered trades to CSV for ML training."""
    if not trades:
        print("No valid trades generated. Adjust parameters or increase sample size.")
        return
    
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FEATURE_COLUMNS)
        writer.writeheader()
        writer.writerows(trades)
    
    print(f"Saved {len(trades)} labeled samples to {output_path}")
    print(f"Win rate: {sum(t['outcome'] for t in trades)/len(trades):.2%}")
    print(f"Avg PnL: {sum(t['pnl_pct'] for t in trades)/len(trades):.2%}")


if __name__ == "__main__":
    print("Generating synthetic market data...")
    raw_data = generate_synthetic_market_data(num_samples=5000, asset="BTC")
    
    print("Running backtest through SignalEngine filters...")
    valid_trades = run_backtest(raw_data)
    
    print(f"Filtered to {len(valid_trades)} valid signals from {len(raw_data)} samples")
    save_training_data(valid_trades)
