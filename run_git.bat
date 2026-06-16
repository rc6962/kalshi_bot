@echo off
cd /d "c:\Tradingbots\kalshi_bot"
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Fix NoneType formatting error in get_positions_report.py"
"C:\Program Files\Git\cmd\git.exe" push
