@echo off
echo Starting Kalshi Bot in Auto-Restart Mode...
:loop
python main.py
echo Bot crashed or was manually stopped (Exit Code: %errorlevel%).
echo Restarting in 5 seconds... Press Ctrl+C to stop completely.
timeout /t 5
goto loop
