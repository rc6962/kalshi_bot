"""
Audit Script: Measures ML veto filter effectiveness by comparing
vetoed signals against actual market outcomes.

Usage: python ml/audit_veto_filter.py [--veto-log ml_veto_log.csv] [--trades trades.csv]
"""
import os
import sys
import csv
import argparse
from datetime import datetime, timezone
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_veto_log(path="ml_veto_log.csv"):
    """Load vetoed signals from CSV."""
    if not os.path.exists(path):
        print(f"No veto log found at {path}. Run the bot first to accumulate vetoed signals.")
        return []
    
    vetoes = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                vetoes.append({
                    "timestamp": row["timestamp"],
                    "signal": row["signal"],
                    "ml_probability": float(row["ml_probability"]) if row["ml_probability"] != "N/A" else None,
                    "multiplier": float(row["multiplier"]),
                    "strike_distance_pct": float(row["strike_distance_pct"]),
                    "recent_move_pct": float(row["recent_move_pct"]),
                    "time_remaining_sec": int(row["time_remaining_sec"]),
                    "futures_trend": float(row["futures_trend"]),
                    "veto_reason": row["veto_reason"]
                })
            except (ValueError, KeyError) as e:
                print(f"Skipping malformed veto row: {e}")
                continue
    
    print(f"Loaded {len(vetoes)} vetoed signals from {path}")
    return vetoes


def load_executed_trades(path="trades.csv"):
    """Load executed trades for baseline comparison."""
    if not os.path.exists(path):
        print(f"No trade log found at {path}.")
        return []
    
    trades = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                trades.append({
                    "datetime": row.get("datetime", ""),
                    "asset": row.get("asset", ""),
                    "side": row.get("side", ""),
                    "profit_usd": float(row.get("profit_usd", 0)),
                    "profit_pct": float(row.get("profit_pct", 0)),
                    "outcome": row.get("outcome", "")
                })
            except (ValueError, KeyError):
                continue
    
    print(f"Loaded {len(trades)} executed trades from {path}")
    return trades


def analyze_vetoes(vetoes):
    """Analyze veto patterns and potential missed opportunities."""
    if not vetoes:
        print("\n=== VETO ANALYSIS ===")
        print("No vetoed signals to analyze yet.")
        return
    
    print("\n=== VETO FILTER AUDIT ===")
    print(f"Total vetoed signals: {len(vetoes)}")
    
    # Breakdown by signal type
    yes_vetoes = [v for v in vetoes if v["signal"] == "ENTER_YES"]
    no_vetoes = [v for v in vetoes if v["signal"] == "ENTER_NO"]
    print(f"  ENTER_YES vetoed: {len(yes_vetoes)}")
    print(f"  ENTER_NO vetoed:  {len(no_vetoes)}")
    
    # Probability distribution of vetoed signals
    probs = [v["ml_probability"] for v in vetoes if v["ml_probability"] is not None]
    if probs:
        avg_prob = sum(probs) / len(probs)
        min_prob = min(probs)
        max_prob = max(probs)
        print(f"\nML Probability of vetoed signals:")
        print(f"  Average: {avg_prob:.3f}")
        print(f"  Range:   [{min_prob:.3f}, {max_prob:.3f}]")
        
        # Signals very close to threshold (potential false negatives)
        near_threshold = [p for p in probs if 0.55 <= p < 0.60]
        print(f"  Near-threshold (0.55-0.60): {len(near_threshold)} signals")
        if near_threshold:
            print("    ⚠️  These might be profitable trades being incorrectly filtered.")
    
    # Feature patterns in vetoed signals
    print("\nFeature averages of vetoed signals:")
    features = ["multiplier", "strike_distance_pct", "recent_move_pct", "time_remaining_sec", "futures_trend"]
    for feat in features:
        vals = [v[feat] for v in vetoes]
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {feat}: {avg:.4f}")
    
    # Veto reasons breakdown
    reasons = defaultdict(int)
    for v in vetoes:
        reason_key = v["veto_reason"].split("confidence")[0].strip() if "confidence" in v["veto_reason"] else v["veto_reason"]
        reasons[reason_key] += 1
    print("\nVeto reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")


def compare_baseline(trades):
    """Show baseline performance without ML filter."""
    if not trades:
        print("\n=== BASELINE PERFORMANCE ===")
        print("No executed trades to analyze.")
        return
    
    wins = sum(1 for t in trades if t["outcome"] == "WIN" or t["profit_usd"] > 0)
    losses = len(trades) - wins
    win_rate = wins / len(trades) if trades else 0
    total_pnl = sum(t["profit_usd"] for t in trades)
    avg_pnl = total_pnl / len(trades) if trades else 0
    
    print("\n=== BASELINE PERFORMANCE (Rule-Based Only) ===")
    print(f"Total trades: {len(trades)}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win rate: {win_rate:.1%}")
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Avg PnL/trade: ${avg_pnl:.2f}")
    
    # Profit factor
    gross_profit = sum(t["profit_usd"] for t in trades if t["profit_usd"] > 0)
    gross_loss = abs(sum(t["profit_usd"] for t in trades if t["profit_usd"] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    print(f"Profit factor: {profit_factor:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Audit ML veto filter effectiveness")
    parser.add_argument("--veto-log", default="ml_veto_log.csv", help="Path to veto log CSV")
    parser.add_argument("--trades", default="trades.csv", help="Path to executed trades CSV")
    args = parser.parse_args()
    
    print("=" * 60)
    print("KALSHI BOT ML FILTER AUDIT REPORT")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    vetoes = load_veto_log(args.veto_log)
    trades = load_executed_trades(args.trades)
    
    analyze_vetoes(vetoes)
    compare_baseline(trades)
    
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS")
    print("=" * 60)
    
    if len(vetoes) < 20:
        print("• Insufficient veto data. Run bot for 24+ hours before auditing.")
    else:
        probs = [v["ml_probability"] for v in vetoes if v["ml_probability"] is not None]
        near_miss = [p for p in probs if 0.55 <= p < 0.60]
        if len(near_miss) > len(vetoes) * 0.3:
            print("• ⚠️  High near-threshold veto rate. Consider lowering ML_CONFIDENCE_THRESHOLD to 0.55")
        elif len(near_miss) < len(vetoes) * 0.05:
            print("• ✓ Threshold appears well-calibrated. Few borderline rejections.")
        
        if trades:
            wins = sum(1 for t in trades if t["profit_usd"] > 0)
            win_rate = wins / len(trades) if trades else 0
            if win_rate < 0.45:
                print("• ⚠️  Baseline win rate < 45%. ML filter may need retraining on more data.")
            elif win_rate > 0.55:
                print("• ✓ Baseline strategy is profitable. ML filter should improve edge further.")
    
    print("\nNext audit recommended after 100+ vetoed signals or 50+ executed trades.")


if __name__ == "__main__":
    main()
