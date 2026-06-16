@echo off
cd /d "c:\Tradingbots\kalshi_bot"
del verify_imports.bat 2>nul
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Add regime detection (RANGE/TREND/HIGH_VOL/SHOCK) with entry/exit/sizing adjustments"
"C:\Program Files\Git\cmd\git.exe" push
