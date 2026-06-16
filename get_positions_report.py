# get_positions_report.py
import asyncio
import os
import sys

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.kalshi_client import KalshiClient
from config import KALSHI_BASE_URL

def load_env():
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

async def main():
    load_env()
    api_key = os.getenv("KALSHI_API_KEY")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    
    if not api_key:
        print("ERROR: KALSHI_API_KEY not found in env.")
        return

    kalshi = KalshiClient(api_key, private_key_path, KALSHI_BASE_URL)
    
    print("\nFetching open positions from Kalshi...")
    positions = await kalshi.get_open_positions()
    
    if not positions:
        print("\nNo active positions found in your portfolio.")
        await kalshi.close()
        return

    print(f"Found {len(positions)} active position(s). Querying market details...")
    
    report_lines = []
    report_lines.append("=" * 110)
    report_lines.append(f"{'Ticker':<30} | {'Side':<4} | {'Qty':<4} | {'Entry':<6} | {'Cost':<7} | {'Bid':<6} | {'CurVal':<7} | {'PnL ($)':<8} | {'PnL (%)':<8} | {'Max Payout':<10}")
    report_lines.append("=" * 110)
    
    total_cost = 0.0
    total_value = 0.0
    total_payout = 0.0
    
    for pos in positions:
        ticker = pos.get("market_ticker") or pos.get("ticker")
        if ticker is None:
            ticker = "Unknown"
        side = (pos.get("side") or pos.get("outcome_side") or "yes").lower()
        qty = abs(int(float(pos.get("position") or pos.get("count") or pos.get("contracts") or 0)))
        
        # Parse entry price
        entry_price = pos.get("entry_price") or pos.get("average_fill_price") or pos.get("avg_entry_price") or pos.get("price") or 0.0
        try:
            entry_price = float(entry_price)
            if entry_price > 1.0:
                entry_price /= 100.0
        except Exception:
            entry_price = 0.0
            
        qty = abs(int(float(pos.get("position") or pos.get("count") or pos.get("contracts") or 0)))
        cost = entry_price * qty
        total_cost += cost
        
        # Query current market price (bid/ask) from Kalshi REST API
        current_bid = 0.0
        if ticker != "Unknown":
            try:
                market_data = await kalshi.authenticated_request("GET", f"/markets/{ticker}")
                market = market_data.get("market") or market_data or {}
                
                # Extract bid/ask
                yes_bid = market.get("yes_bid") or market.get("yes_bid_dollars")
                no_bid = market.get("no_bid") or market.get("no_bid_dollars")
                
                if yes_bid is not None:
                    try:
                        yes_bid = float(yes_bid)
                        if yes_bid > 1.0:
                            yes_bid /= 100.0
                    except Exception:
                        yes_bid = 0.0
                else:
                    yes_bid = 0.0
                    
                if no_bid is not None:
                    try:
                        no_bid = float(no_bid)
                        if no_bid > 1.0:
                            no_bid /= 100.0
                    except Exception:
                        no_bid = 0.0
                else:
                    no_bid = 0.0
                
                if side == "yes":
                    current_bid = yes_bid if yes_bid is not None else 0.0
                else:
                    current_bid = no_bid if no_bid is not None else 0.0
            except Exception as err:
                print(f"  Warning: Failed to fetch market data for {ticker}: {err}")
                current_bid = entry_price # fallback to entry
        else:
            current_bid = entry_price
            
        cur_val = current_bid * qty
        total_value += cur_val
        
        payout = qty * 1.00
        total_payout += payout
        
        pnl_usd = cur_val - cost
        pnl_pct = (pnl_usd / cost * 100.0) if cost > 0 else 0.0
        
        # Safe variables for string formatting
        ticker_str = str(ticker) if ticker is not None else "Unknown"
        side_str = str(side).upper() if side is not None else "YES"
        qty_val = int(qty) if qty is not None else 0
        entry_val = float(entry_price) if entry_price is not None else 0.0
        cost_val = float(cost) if cost is not None else 0.0
        bid_val = float(current_bid) if current_bid is not None else 0.0
        cur_val_val = float(cur_val) if cur_val is not None else 0.0
        pnl_usd_val = float(pnl_usd) if pnl_usd is not None else 0.0
        pnl_pct_val = float(pnl_pct) if pnl_pct is not None else 0.0
        payout_val = float(payout) if payout is not None else 0.0

        report_lines.append(
            f"{ticker_str:<30} | {side_str:<4} | {qty_val:<4} | ${entry_val:<5.2f} | ${cost_val:<6.2f} | ${bid_val:<5.2f} | ${cur_val_val:<6.2f} | ${pnl_usd_val:<7.2f} | {pnl_pct_val:>7.1f}% | ${payout_val:<9.2f}"
        )
        
    report_lines.append("=" * 110)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0
    
    total_cost_val = float(total_cost) if total_cost is not None else 0.0
    total_value_val = float(total_value) if total_value is not None else 0.0
    total_pnl_val = float(total_pnl) if total_pnl is not None else 0.0
    total_pnl_pct_val = float(total_pnl_pct) if total_pnl_pct is not None else 0.0
    total_payout_val = float(total_payout) if total_payout is not None else 0.0

    report_lines.append(
        f"{'TOTALS':<30} | {'':<4} | {'':<4} | {'':<6} | ${total_cost_val:<6.2f} | {'':<6} | ${total_value_val:<6.2f} | ${total_pnl_val:<7.2f} | {total_pnl_pct_val:>7.1f}% | ${total_payout_val:<9.2f}"
    )
    report_lines.append("=" * 110)
    
    # Also fetch and print portfolio balance
    balance = await kalshi.get_balance()
    if balance is not None:
        report_lines.append(f"Portfolio Balance: ${balance:.2f}")
    
    report_str = "\n".join(report_lines)
    print(report_str)
    
    # Save to file as well
    with open("positions_report.txt", "w") as f:
        f.write(report_str)
    print("\nReport saved to positions_report.txt")
    
    await kalshi.close()

if __name__ == "__main__":
    asyncio.run(main())
