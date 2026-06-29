@echo off
cd /d "C:\Users\hidan\Documents\stock-bot"
set PYTHONIOENCODING=utf-8

echo ========================================
echo   بوت الأسهم - Stock Advisor Bot
echo ========================================
echo.

:restart
echo [%date% %time%] تشغيل البوت...
echo.

"C:\Users\hidan\AppData\Local\Programs\Python\Python312\python.exe" main.py >> bot_output.log 2>&1

echo.
echo [%date% %time%] البوت توقف بشكل غير متوقع!
echo اضغط Ctrl+C للإيقاف، أو سيعاد التشغيل تلقائياً...
echo.
timeout /t 5 /nobreak >nul
goto restart
