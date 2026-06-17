import csv
from collections import defaultdict

filename = "Kalshi-Recent-Activity-All (2).csv"

total_contracts = 0
total_cost = 0.0

asset_contracts = defaultdict(int)
asset_cost = defaultdict(float)

with open(filename, "r", encoding="utf-8-sig") as f:
    content = f.read().replace('\ufeff', '')
    lines = content.splitlines()
    if not lines:
        exit(0)
        
    reader = csv.DictReader(lines)
    for row in reader:
        cleaned_row = {k.strip(' "'): v for k, v in row.items() if k is not None}
        row_type = cleaned_row.get("type", "")
        ticker = cleaned_row.get("Market_Ticker", "")
        if not ticker:
            continue
            
        asset = ticker.split("15M")[0].replace("KX", "") if "15M" in ticker else ticker
        
        if row_type == "Trade":
            try:
                # "Price_In_Cents" contains the entry price per contract
                price_cents = float(cleaned_row.get("Price_In_Cents") or 0)
                # "No_Contracts_Owned" or "Yes_Contracts_Owned" gives the number
                side = cleaned_row.get("Direction", "").lower()
                
                # To get exact contracts traded in this line, usually Kalshi has Amount_In_Dollars / (Price_In_Cents / 100)
                # Let's derive it from Amount_In_Dollars and Price_In_Cents
                amt = float(cleaned_row.get("Amount_In_Dollars") or 0)
                if price_cents > 0:
                    contracts = round(amt / (price_cents / 100))
                    total_contracts += contracts
                    total_cost += amt
                    
                    asset_contracts[asset] += contracts
                    asset_cost[asset] += amt
            except Exception:
                pass

print("=== AVERAGE ENTRY PRICES ===")
avg_entry = (total_cost / total_contracts) if total_contracts > 0 else 0
print(f"Overall Average Entry Price: ${avg_entry:.2f} ({total_contracts} contracts)")

for asset in sorted(asset_contracts.keys()):
    c = asset_contracts[asset]
    cost = asset_cost[asset]
    avg = (cost / c) if c > 0 else 0
    print(f"{asset:5} | Avg Entry: ${avg:.2f} | Contracts: {c}")
