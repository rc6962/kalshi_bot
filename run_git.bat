@echo off
cd /d "c:\Tradingbots\kalshi_bot"
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Fix websocket print spam and main.py dotenv loading order"
"C:\Program Files\Git\cmd\git.exe" push
