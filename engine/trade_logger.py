import csv
import os
import time
from datetime import datetime, timezone
from collections import defaultdict

TRADE_CSV_PATH = "trades.csv"


class TradeLogger:
    def __init__(self, csv_path=TRADE_CSV_PATH):
        self.csv_path = csv_path
        self.daily_trades = []
        self.portfolio_start = None
        self.portfolio_current = None
        self._sheet = None
        self._portfolio_balance_ref = None  # Reference to shared portfolio balance

        # Initialize the CSV file with headers if it doesn't exist
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "trade_id", "datetime", "asset", "window_start", "window_end",
                    "side", "entry_price", "exit_price", "contracts",
                    "profit_usd", "profit_pct", "outcome",
                    "entry_time", "exit_time", "held_seconds", "exit_reason",
                    "multiplier", "strike_distance_pct", "recent_move_pct",
                    "time_remaining_sec", "futures_trend", "spot_price", "strike_price"
                ])

    def _safe_update(self, ws, range_name, values):
        import gspread
        try:
            major = int(gspread.__version__.split('.')[0])
        except Exception:
            major = 5
        if major >= 6:
            ws.update(values, range_name)
        else:
            ws.update(range_name, values)

    def try_init_sheet(self):
        if self._sheet is not None:
            return
        try:
            import gspread
            from google.oauth2 import service_account
            import json, os
            creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
            sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
            if not sheet_id or sheet_id == "your_google_sheet_id_here":
                print("Google Sheets not configured. Please set GOOGLE_SHEET_ID in .env and ensure google_credentials.json exists.")
                return
            if not os.path.exists(creds_path):
                print(f"Google credentials file not found: {creds_path}")
                return
            creds = service_account.Credentials.from_service_account_file(
                creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            self._sheet = client.open_by_key(sheet_id)
            self._init_sheet_layout()
            print("Google Sheets integration initialized successfully!")
        except ImportError:
            print("gspread not installed. Install with: pip install gspread google-auth")
        except Exception as e:
            print(f"Error initializing Google Sheet logger: {e}")
            import traceback
            traceback.print_exc()
            self._sheet = None

    def _init_sheet_layout(self):
        try:
            sheet1 = self._sheet.worksheet("Live Trades")
        except Exception:
            sheet1 = self._sheet.add_worksheet("Live Trades", 1000, 20)

        try:
            sheet2 = self._sheet.worksheet("Asset Summary")
        except Exception:
            sheet2 = self._sheet.add_worksheet("Asset Summary", 1000, 20)

        sheet1.clear()
        sheet2.clear()

        sheet1.format("A1:J1", {"textFormat": {"bold": True}})
        sheet1.format("A1:J1", {"backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2}})
        sheet1.format("A1:J1", {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}})

        sheet2.format("A1:F1", {"textFormat": {"bold": True}})
        sheet2.format("A1:F1", {"backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2}})
        sheet2.format("A1:F1", {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}})

        self._row_offsets = {"sheet1": 1, "sheet2": 1}
        self._write_trade_headers(sheet1)
        self._write_asset_headers(sheet2)
        self._sheet1 = sheet1
        self._sheet2 = sheet2

    def _write_trade_headers(self, ws):
        headers = [
            ["#", "Asset", "Outcome", "Profit ($)", "Profit %",
             "Buy Price", "Sell Price", "Contracts", "Entry Time", "Exit Time"]
        ]
        self._safe_update(ws, "A1:J1", headers)
        ws.format("A1:J1", {
            "textFormat": {"bold": True, "fontSize": 11},
            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.25},
            "horizontalAlignment": "CENTER",
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}
        })

    def _write_asset_headers(self, ws):
        headers = [["Asset", "Outcome", "PnL ($)", "PnL (%)", "Trades", "Win Rate"]]
        self._safe_update(ws, "A1:F1", headers)
        ws.format("A1:F1", {
            "textFormat": {"bold": True, "fontSize": 11},
            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.25},
            "horizontalAlignment": "CENTER",
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}
        })

    def update_portfolio_value(self, value: float):
        # Use the shared portfolio balance reference if available
        if self._portfolio_balance_ref and self._portfolio_balance_ref.get("value") is not None:
            value = self._portfolio_balance_ref["value"]
        
        if self.portfolio_start is None:
            self.portfolio_start = value
        self.portfolio_current = value

    def log_trade(self, trade: dict):
        self.daily_trades.append(trade)

        with open(self.csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.get("trade_id"),
                trade.get("datetime"),
                trade.get("asset"),
                trade.get("window_start"),
                trade.get("window_end"),
                trade.get("side"),
                trade.get("entry_price"),
                trade.get("exit_price"),
                trade.get("contracts"),
                trade.get("profit_usd"),
                trade.get("profit_pct"),
                trade.get("outcome"),
                trade.get("entry_time"),
                trade.get("exit_time"),
                trade.get("held_seconds"),
                trade.get("exit_reason"),
                # ML Features
                trade.get("multiplier"),
                trade.get("strike_distance_pct"),
                trade.get("recent_move_pct"),
                trade.get("time_remaining_sec"),
                trade.get("futures_trend"),
                trade.get("spot_price"),
                trade.get("strike_price"),
            ])

        self.try_init_sheet()
        self._update_gsheet()

        # Count total rows in trades.csv to determine fill count
        fill_count = 0
        if os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, "r") as f:
                    # Subtract 1 for the header
                    fill_count = sum(1 for line in f) - 1
            except Exception:
                fill_count = len(self.daily_trades)

        if fill_count > 0 and fill_count % 100 == 0:
            try:
                import subprocess
                import sys
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                script_path = os.path.join(base_dir, "ml", "train_model.py")
                print(f"[TradeLogger] Retraining ML model in background (total fills: {fill_count})...")
                subprocess.Popen(
                    [sys.executable, script_path, "--data", self.csv_path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as bg_err:
                print(f"[TradeLogger] Failed to start background training: {bg_err}")

    def _update_gsheet(self):
        if self._sheet is None:
            return
        try:
            self._rebuild_sheet1()
            self._rebuild_sheet2()
            # Throttle writes to stay within Google Sheets quota (avoid HTTP 429)
            import config as _cfg
            delay = getattr(_cfg, "SHEET_WRITE_DELAY", 1.0)
            if delay > 0:
                import time as _time
                _time.sleep(delay)
        except Exception as e:
            print(f"Error updating Google Sheet: {e}")
            import traceback
            traceback.print_exc()

    def _build_daily_header_row(self):
        # Use the shared portfolio balance reference if available
        if self._portfolio_balance_ref and self._portfolio_balance_ref.get("value") is not None:
            current_value = self._portfolio_balance_ref["value"]
        else:
            current_value = self.portfolio_current
        
        start = self.portfolio_start or 0
        current = current_value or 0
        pnl = current - start
        pnl_pct = (pnl / start * 100) if start else 0
        total_trades = len(self.daily_trades)
        wins = sum(1 for t in self.daily_trades if t.get("outcome") == "WIN")
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades else 0
        outcome = "WIN" if pnl >= 0 else "LOSS"
        return {
            "date": datetime.now().strftime("%m/%d/%Y"),
            "start": start,
            "current": current,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "outcome": outcome,
        }

    def _rebuild_sheet1(self):
        ws = self._sheet1
        daily = self._build_daily_header_row()
        ws.clear()

        today = datetime.now().strftime("%m/%d/%Y")
        outcome_icon = "WIN" if daily["outcome"] == "WIN" else "LOSS"
        rs = [
            [f"DAILY SUMMARY — {today}"],
            [f"Start: ${daily['start']:.2f}  →  End: ${daily['current']:.2f}"],
            [f"PnL: ${daily['pnl']:.2f}  |  {daily['pnl_pct']:.2f}%  |  {outcome_icon}"],
            [f"Trades: {daily['trades']}  |  Wins: {daily['wins']}  |  Losses: {daily['losses']}  |  Win Rate: {daily['win_rate']:.1f}%"],
        ]
        self._safe_update(ws, "A1:J4", rs)
        ws.merge_cells("A1:J1")
        ws.merge_cells("A2:J2")
        ws.merge_cells("A3:J3")
        ws.merge_cells("A4:J4")

        bg_color = {"red": 0.1, "green": 0.6, "blue": 0.2} if daily["outcome"] == "WIN" else {"red": 0.7, "green": 0.1, "blue": 0.1}
        for r in range(1, 5):
            ws.format(f"A{r}:J{r}", {
                "backgroundColor": bg_color,
                "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "CENTER",
            })
        ws.format("A1:J1", {"textFormat": {"fontSize": 16}})

        row = 6
        self._safe_update(ws, f"A{row}:J{row}", [["#", "Asset", "Outcome", "Profit ($)", "Profit %",
                                         "Buy Price", "Sell Price", "Contracts", "Entry Time", "Exit Time"]])
        ws.format(f"A{row}:J{row}", {
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.35},
            "horizontalAlignment": "CENTER",
        })
        row += 1

        windows = defaultdict(list)
        for t in self.daily_trades:
            w = t.get("window_start", "")
            windows[w].append(t)

        for i, (window, trades) in enumerate(sorted(windows.items())):
            ws.merge_cells(f"A{row}:J{row}")
            win_total = sum(t.get("profit_usd", 0) for t in trades)
            win_pct = sum(t.get("profit_pct", 0) for t in trades)
            win_total_pct = (win_total / abs(sum(t.get("entry_price", 0) * t.get("contracts", 0) for t in trades) or 1)) * 100
            section_icon = "WIN" if win_total >= 0 else "LOSS"
            self._safe_update(ws, f"A{row}:J{row}", [[f"{section_icon}  {window}  —  TOTAL: ${win_total:.2f}  ({win_total_pct:.2f}%)"]])
            section_bg = {"red": 0.1, "green": 0.5, "blue": 0.15} if win_total >= 0 else {"red": 0.6, "green": 0.1, "blue": 0.1}
            ws.format(f"A{row}:J{row}", {
                "backgroundColor": section_bg,
                "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "LEFT",
            })
            row += 1

            for t in trades:
                row_data = [
                    t.get("trade_id", ""),
                    t.get("asset", ""),
                    "WIN" if t.get("outcome") == "WIN" else "LOSS",
                    f"${t.get('profit_usd', 0):+.2f}",
                    f"{t.get('profit_pct', 0):+.2f}%",
                    f"${t.get('entry_price', 0):.2f}" if t.get("entry_price") else "-",
                    f"${t.get('exit_price', 0):.2f}" if t.get("exit_price") else "-",
                    t.get("contracts", ""),
                    t.get("entry_time", ""),
                    t.get("exit_time", ""),
                ]
                self._safe_update(ws, f"A{row}:J{row}", [row_data])

                is_win = t.get("outcome") == "WIN"
                row_bg = {"red": 0.9, "green": 1, "blue": 0.9} if is_win else {"red": 1, "green": 0.9, "blue": 0.9}
                ws.format(f"A{row}:J{row}", {"backgroundColor": row_bg, "horizontalAlignment": "CENTER"})

                if t.get("profit_usd", 0) >= 0:
                    ws.format(f"D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0.4, "blue": 0}}})
                else:
                    ws.format(f"D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0.7, "green": 0, "blue": 0}}})
                row += 1

            row += 1

        ws.set_basic_filter(f"A5:J{row - 1}")

    def _rebuild_sheet2(self):
        ws = self._sheet2
        ws.clear()

        daily = self._build_daily_header_row()
        row = 1
        today = datetime.now().strftime("%m/%d/%Y")
        outcome_icon = "WIN" if daily["outcome"] == "WIN" else "LOSS"

        ws.merge_cells(f"A{row}:F{row}")
        self._safe_update(ws, f"A{row}:F{row}", [[f"DAILY ASSET SUMMARY — {today}"]])
        ws.format(f"A{row}:F{row}", {
            "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.4},
            "textFormat": {"bold": True, "fontSize": 16, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
        })
        row += 1

        asset_totals = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for t in self.daily_trades:
            a = t.get("asset", "")
            asset_totals[a]["pnl"] += t.get("profit_usd", 0)
            asset_totals[a]["trades"] += 1
            if t.get("outcome") == "WIN":
                asset_totals[a]["wins"] += 1

        ws.merge_cells(f"A{row}:F{row}")
        ws.update(range_name=f"A{row}:F{row}", values=[[f"DAY TOTAL: {daily['pnl']:+.2f}%  |  {daily['pnl_pct']:.2f}%  |  {daily['trades']} trades  |  {daily['win_rate']:.1f}% win rate  |  {outcome_icon}"]])
        ws.format(f"A{row}:F{row}", {
            "backgroundColor": {"red": 0.15, "green": 0.6, "blue": 0.2} if daily["outcome"] == "WIN" else {"red": 0.7, "green": 0.1, "blue": 0.1},
            "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
        })
        row += 1

        ws.update(range_name=f"A{row}:F{row}", values=[["Asset", "Outcome", "PnL ($)", "PnL (%)", "Trades", "Win Rate"]])
        ws.format(f"A{row}:F{row}", {
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.35},
            "horizontalAlignment": "CENTER",
        })
        row += 1

        for asset, data in sorted(asset_totals.items()):
            pnl_pct = (data["pnl"] / (daily["start"] or 1)) * 100
            win_rate = (data["wins"] / data["trades"] * 100) if data["trades"] else 0
            outcome = "WIN" if data["pnl"] >= 0 else "LOSS"
            self._safe_update(ws, f"A{row}:F{row}", [[
                asset, outcome,
                f"${data['pnl']:+.2f}", f"{pnl_pct:+.2f}%",
                data["trades"], f"{win_rate:.1f}%"
            ]])
            is_pos = data["pnl"] >= 0
            row_bg = {"red": 0.9, "green": 1, "blue": 0.9} if is_pos else {"red": 1, "green": 0.9, "blue": 0.9}
            ws.format(f"A{row}:F{row}", {"backgroundColor": row_bg, "horizontalAlignment": "CENTER"})
            if is_pos:
                ws.format(f"C{row}:D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0.4, "blue": 0}}})
            else:
                ws.format(f"C{row}:D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0.7, "green": 0, "blue": 0}}})
            row += 1

        ws.update(range_name=f"A{row}:F{row}", values=[[
            "TOTAL", daily["outcome"],
            f"${daily['pnl']:+.2f}", f"{daily['pnl_pct']:.2f}%",
            daily["trades"], f"{daily['win_rate']:.1f}%"
        ]])
        ws.format(f"A{row}:F{row}", {
            "textFormat": {"bold": True, "fontSize": 11},
            "backgroundColor": {"red": 1, "green": 0.95, "blue": 0.8},
            "horizontalAlignment": "CENTER",
        })
        row += 2

        windows = defaultdict(list)
        for t in self.daily_trades:
            w = t.get("window_start", "")
            windows[w].append(t)

        for window, trades in sorted(windows.items()):
            ws.merge_cells(f"A{row}:F{row}")
            win_total = sum(t.get("profit_usd", 0) for t in trades)
            section_icon = "WIN" if win_total >= 0 else "LOSS"
            ws.update(range_name=f"A{row}:F{row}", values=[[f"{section_icon}  {window}  —  Total: ${win_total:.2f}"]])
            section_bg = {"red": 0.1, "green": 0.5, "blue": 0.15} if win_total >= 0 else {"red": 0.6, "green": 0.1, "blue": 0.1}
            ws.format(f"A{row}:F{row}", {
                "backgroundColor": section_bg,
                "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "LEFT",
            })
            row += 1

            ws.update(range_name=f"A{row}:F{row}", values=[["Asset", "Outcome", "PnL ($)", "PnL (%)", "Trades", "Win Rate"]])
            ws.format(f"A{row}:F{row}", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
                "horizontalAlignment": "CENTER",
            })
            row += 1

            per_asset = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
            for t in trades:
                a = t.get("asset", "")
                per_asset[a]["pnl"] += t.get("profit_usd", 0)
                per_asset[a]["trades"] += 1
                if t.get("outcome") == "WIN":
                    per_asset[a]["wins"] += 1

            for asset, data in sorted(per_asset.items()):
                pnl_pct = (data["pnl"] / (daily["start"] or 1)) * 100
                win_rate = (data["wins"] / data["trades"] * 100) if data["trades"] else 0
                outcome = "WIN" if data["pnl"] >= 0 else "LOSS"
                ws.update(range_name=f"A{row}:F{row}", values=[[
                    asset, outcome,
                    f"${data['pnl']:+.2f}", f"{pnl_pct:+.2f}%",
                    data["trades"], f"{win_rate:.1f}%"
                ]])
                is_pos = data["pnl"] >= 0
                row_bg = {"red": 0.9, "green": 1, "blue": 0.9} if is_pos else {"red": 1, "green": 0.9, "blue": 0.9}
                ws.format(f"A{row}:F{row}", {"backgroundColor": row_bg, "horizontalAlignment": "CENTER"})
                if is_pos:
                    ws.format(f"C{row}:D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0.4, "blue": 0}}})
                else:
                    ws.format(f"C{row}:D{row}", {"textFormat": {"bold": True, "foregroundColor": {"red": 0.7, "green": 0, "blue": 0}}})
                row += 1

            row += 1