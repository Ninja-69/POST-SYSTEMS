@echo off
title FundedAI Telegram Cloud Bot
cd /d "%~dp0"
echo 🤖 Starting Telegram Delivery Server...
:loop
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" telegram_bot.py
echo ⚠️ Bot crashed or stopped. Restarting in 5 seconds...
timeout /t 5
goto loop
