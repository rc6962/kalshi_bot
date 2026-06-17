import csv
from collections import defaultdict

filename = "Kalshi-Recent-Activity-All (2).csv"

trades = []
settlements = []

asset_pnl = defaultdict(float)
asset_wins = defaultdict(int)
asset_losses = defaultdict(int)

total_cost = 0.0
total_payout = 0.0
total_fee = 0.0

market_costs = defaultdict(float)
asset_cost = defaultdict(float)

with open(filename, "r", encoding="utf-8-sig") as f:
    # Read the file and strip BOM and whitespace from headers
    content = f.read().replace('\ufeff', '')
    lines = content.splitlines()
    
    if not lines:
        print("Empty file")
        exit(0)
        
    reader = csv.DictReader(lines)
    for row in reader:
        # Clean keys
        cleaned_row = {k.strip(' "'): v for k, v in row.items() if k is not None}
        
        row_type = cleaned_row.get("type", "")
        ticker = cleaned_row.get("Market_Ticker", "")
        if not ticker:
            continue
            
        asset = ticker.split("15M")[0].replace("KX", "") if "15M" in ticker else ticker
        
        if row_type == "Trade":
            try:
                amt = float(cleaned_row.get("Amount_In_Dollars") or 0)
                fee = float(cleaned_row.get("Fee_In_Dollars") or 0)
                total_cost += amt
                total_fee += fee
                market_costs[ticker] += amt + fee
                asset_cost[asset] += amt + fee
                asset_pnl[asset] -= (amt + fee)
            except Exception:
                pass
        elif row_type == "Settlement":
            try:
                profit = float(cleaned_row.get("Profit_In_Dollars") or 0)
                # For Kalshi, settlement profit is the total payout.
                # Since we already subtracted the initial cost in the Trade row, 
                # adding the payout gives the net PnL.
                total_payout += profit
                asset_pnl[asset] += profit
                
                # A win is if the settlement payout is greater than zero
                if profit > 0:
                    asset_wins[asset] += 1
                else:
                    asset_losses[asset] += 1
            except Exception as e:
                pass

print("=== OVERALL PERFORMANCE ===")
print(f"Total Spent on Trades: ${total_cost:.2f}")
print(f"Total Fees: ${total_fee:.2f}")
print(f"Total Payouts: ${total_payout:.2f}")
total_pnl = total_payout - total_cost - total_fee
print(f"Net PnL: ${total_pnl:.2f}")

print("\n=== ASSET BREAKDOWN ===")
# Sort by PnL
for asset, pnl in sorted(asset_pnl.items(), key=lambda x: x[1]):
    w = asset_wins[asset]
    l = asset_losses[asset]
    total_trades = w + l
    win_rate = (w / total_trades * 100) if total_trades > 0 else 0
    spent = asset_cost[asset]
    print(f"{asset:5} | PnL: ${pnl:>6.2f} | Win Rate: {win_rate:>5.1f}% ({w}W/{l}L) | Spent: ${spent:.2f}")
