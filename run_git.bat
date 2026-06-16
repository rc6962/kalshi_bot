@echo off
cd /d "c:\Tradingbots\kalshi_bot"
"C:\Program Files\Git\cmd\git.exe" rm --cached bot.log 2>nul
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "Untrack bot.log from Git repository"
"C:\Program Files\Git\cmd\git.exe" push
