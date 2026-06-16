import pandas as pd
import numpy as np

df = pd.read_csv('ml_training_data.csv')

# Detailed analysis of the strategy
print("=" * 60)
print("COMPREHENSIVE STRATEGY ANALYSIS")
print("=" * 60)

# Overall stats
print(f"\nTotal samples: {len(df)}")
print(f"Overall win rate: {(df['outcome']==1).mean():.2%}")
print(f"Avg PnL: {df['pnl_pct'].mean():.4f}")
print(f"Median PnL: {df['pnl_pct'].median():.4f}")

# The key issue: median is -1.0 (loss), mean is positive
# This means most trades lose but winners win big
# This is characteristic of binary options with high multipliers

print("\n" + "=" * 60)
print("WIN/LOSS DISTRIBUTION")
print("=" * 60)
wins = df[df['outcome']==1]
losses = df[df['outcome']==0]
print(f"Wins: {len(wins)} ({len(wins)/len(df):.2%}), Avg win: {wins['pnl_pct'].mean():.4f}, Max win: {wins['pnl_pct'].max():.4f}")
print(f"Losses: {len(losses)} ({len(losses)/len(df):.2%}), Avg loss: {losses['pnl_pct'].mean():.4f}")

# Expected value per trade
ev = df['pnl_pct'].mean()
print(f"\nExpected Value per trade: {ev:.4f}")
print(f"Expected Value per trade (in $): ${ev * 1:.4f} per contract")

# Current config parameters
print("\n" + "=" * 60)
print("CURRENT CONFIG ANALYSIS")
print("=" * 60)
print(f"IMPULSE_THRESHOLD_PCT: 0.0003 (0.03%)")
print(f"MIN_MULTIPLIER: 2.5")
print(f"MAX_MULTIPLIER: 100")
print(f"STRIKE_PROXIMITY_PCT: 0.10 (10%)")
print(f"NO_ENTRY_LAST_SECONDS: 180")
print(f"STOP_LOSS_PCT: -0.15")
print(f"PROFIT_PROTECTION_TRIGGER: 0.35")

# What if we only trade the best setups?
print("\n" + "=" * 60)
print("OPTIMIZATION SCENARIOS")
print("=" * 60)

# Scenario 1: Only trade when multiplier > 3.5 (higher expectancy)
high_mult = df[df['multiplier'] > 3.5]
print(f"\n1. Multiplier > 3.5: {len(high_mult)} trades, WR: {(high_mult['outcome']==1).mean():.2%}, Avg PnL: {high_mult['pnl_pct'].mean():.4f}")

# Scenario 2: Only trade YES side (better win rate)
yes_side = df[df['side'] == 'yes']
print(f"2. YES side only: {len(yes_side)} trades, WR: {(yes_side['outcome']==1).mean():.2%}, Avg PnL: {yes_side['pnl_pct'].mean():.4f}")

# Scenario 3: Only trade when recent_move_pct < -0.0005 (stronger signal)
strong_yes = df[df['recent_move_pct'] < -0.0005]
print(f"3. Strong YES (< -0.05%): {len(strong_yes)} trades, WR: {(strong_yes['outcome']==1).mean():.2%}, Avg PnL: {strong_yes['pnl_pct'].mean():.4f}")

# Scenario 4: Combine - YES side with strong signal and high multiplier
combo = df[(df['side'] == 'yes') & (df['recent_move_pct'] < -0.0004) & (df['multiplier'] > 3.0)]
print(f"4. Combo (YES + strong move + mult>3): {len(combo)} trades, WR: {(combo['outcome']==1).mean():.2%}, Avg PnL: {combo['pnl_pct'].mean():.4f}")

# Scenario 5: Only trade near ATM (strike_distance < 0.05)
atm = df[df['strike_distance_pct'] < 0.05]
print(f"5. Near ATM (strike_dist < 5%): {len(atm)} trades, WR: {(atm['outcome']==1).mean():.2%}, Avg PnL: {atm['pnl_pct'].mean():.4f}")

# Scenario 6: Time remaining > 300 seconds
time_ok = df[df['time_remaining_sec'] > 300]
print(f"6. Time > 300s: {len(time_ok)} trades, WR: {(time_ok['outcome']==1).mean():.2%}, Avg PnL: {time_ok['pnl_pct'].mean():.4f}")

# Scenario 7: Contract price sweet spot (0.25-0.40)
sweet = df[(df['contract_price'] >= 0.25) & (df['contract_price'] <= 0.40)]
print(f"7. Contract price 0.25-0.40: {len(sweet)} trades, WR: {(sweet['outcome']==1).mean():.2%}, Avg PnL: {sweet['pnl_pct'].mean():.4f}")

# Best combo
best = df[
    (df['side'] == 'yes') & 
    (df['recent_move_pct'] < -0.0004) & 
    (df['multiplier'] > 3.0) & 
    (df['strike_distance_pct'] < 0.06) &
    (df['contract_price'] >= 0.25) & 
    (df['contract_price'] <= 0.40) &
    (df['time_remaining_sec'] > 200)
]
print(f"\n8. BEST COMBO: {len(best)} trades, WR: {(best['outcome']==1).mean():.2%}, Avg PnL: {best['pnl_pct'].mean():.4f}")

# Calculate Kelly criterion
if len(best) > 0:
    p = (best['outcome']==1).mean()
    avg_win = best[best['outcome']==1]['pnl_pct'].mean()
    avg_loss = abs(best[best['outcome']==0]['pnl_pct'].mean())
    b = avg_win / avg_loss if avg_loss > 0 else 0
    kelly = (p * b - (1-p)) / b if b > 0 else 0
    print(f"   Kelly fraction: {kelly:.2%}")
    print(f"   Avg win: {avg_win:.4f}, Avg loss: {avg_loss:.4f}")
    print(f"   Payoff ratio (b): {b:.2f}")

# Feature importance from ML perspective
print("\n" + "=" * 60)
print("FEATURE CORRELATIONS WITH OUTCOME")
print("=" * 60)
for col in ['multiplier', 'strike_distance_pct', 'recent_move_pct', 'time_remaining_sec', 'futures_trend', 'contract_price', 'spot_price']:
    corr = df[col].corr(df['outcome'])
    print(f"{col}: {corr:.4f}")

# Recent move correlation by side
print("\nRecent move correlation by side:")
for side in ['yes', 'no']:
    sub = df[df['side'] == side]
    corr = sub['recent_move_pct'].corr(sub['outcome'])
    print(f"  {side}: {corr:.4f}")

print("\n" + "=" * 60)
print("RECOMMENDATIONS")
print("=" * 60)
print("1. The strategy has positive expectancy (~0.41% per trade) but very high variance")
print("2. Median trade is a loss (-100%), winners average ~2.5x")
print("3. ENTER_YES signals significantly outperform ENTER_NO (53.8% vs 42.3% WR)")
print("4. Consider: Only trade YES side, require stronger impulse (>0.04-0.05%), higher multiplier (>3.5)")
print("5. Position sizing: Use Kelly or half-Kelly (currently fixed $1/contract)")
print("6. Risk management: Current stop loss at -15% may be too tight for binary options")
print("7. ML model should be retrained on real trades (currently only synthetic)")