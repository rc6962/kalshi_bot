import csv

filename = "Kalshi-Recent-Activity-All (2).csv"

with open(filename, "r", encoding="utf-8-sig") as f:
    content = f.read().replace('\ufeff', '')
    lines = content.splitlines()
    reader = csv.DictReader(lines)
    for row in reader:
        cleaned_row = {k.strip(' "'): v for k, v in row.items() if k is not None}
        row_type = cleaned_row.get("type", "")
        if row_type == "Trade":
            try:
                amt = float(cleaned_row.get("Amount_In_Dollars") or 0)
                if amt >= 10.0:
                    ticker = cleaned_row.get("Market_Ticker", "")
                    price = cleaned_row.get("Price_In_Cents", "")
                    date = cleaned_row.get("Traded_Time", "")
                    contracts_no = cleaned_row.get("No_Contracts_Owned", "")
                    contracts_yes = cleaned_row.get("Yes_Contracts_Owned", "")
                    
                    print(f"[{date}] Trade on {ticker} | Amount: ${amt:.2f} | Price: {price}c | Contracts: YES={contracts_yes}, NO={contracts_no}")
            except Exception as e:
                pass
