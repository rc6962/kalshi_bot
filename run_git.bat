@echo off
cd /d "c:\Tradingbots\kalshi_bot"
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Update API key handling (remove defaults)"
"C:\Program Files\Git\cmd\git.exe" push
