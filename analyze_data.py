import pandas as pd
df = pd.read_csv('ml_training_data.csv')

# Look at win rate by signal type
print('=== ENTER_YES (recent_move < -0.0003) ===')
yes_signals = df[df['recent_move_pct'] < -0.0003]
print(f'Count: {len(yes_signals)}, Win rate: {(yes_signals["outcome"]==1).mean():.2%}')

print()
print('=== ENTER_NO (recent_move > 0.0003) ===')
no_signals = df[df['recent_move_pct'] > 0.0003]
print(f'Count: {len(no_signals)}, Win rate: {(no_signals["outcome"]==1).mean():.2%}')

print()
print('=== NO SIGNAL (-0.0003 to 0.0003) ===')
neutral = df[(df['recent_move_pct'] >= -0.0003) & (df['recent_move_pct'] <= 0.0003)]
print(f'Count: {len(neutral)}, Win rate: {(neutral["outcome"]==1).mean():.2%}')

print()
# Check what happens with different thresholds
for thresh in [0.0002, 0.0003, 0.0004, 0.0005, 0.0006]:
    yes = df[df['recent_move_pct'] < -thresh]
    no = df[df['recent_move_pct'] > thresh]
    all_sig = len(yes) + len(no)
    if all_sig > 0:
        wins = (yes['outcome']==1).sum() + (no['outcome']==1).sum()
        print(f'Threshold {thresh}: {all_sig} signals, win rate {wins/all_sig:.2%}')

# Check PnL distribution
print()
print('=== PnL Statistics ===')
print(f'Avg PnL: {df["pnl_pct"].mean():.4f}')
print(f'Median PnL: {df["pnl_pct"].median():.4f}')
print(f'Std PnL: {df["pnl_pct"].std():.4f}')
print(f'Skewness: {df["pnl_pct"].skew():.4f}')

# Check by contract price
print()
print('=== By Contract Price ===')
for bins in [[0, 0.2, 0.3, 0.4, 0.5, 1.0]]:
    print(df.groupby(pd.cut(df['contract_price'], bins=bins))['outcome'].mean())

# Check by side
print()
print('=== By Side ===')
print(df.groupby('side')['outcome'].mean())
print(df.groupby('side')['pnl_pct'].mean())