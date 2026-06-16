@echo off
cd /d "c:\Tradingbots\kalshi_bot"
del verify_imports.bat 2>nul
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Binary market strategy recalibration: convex exit model, probability flow, remove hardcoded win_prob"
"C:\Program Files\Git\cmd\git.exe" push
