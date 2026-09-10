@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Market Monitor - 儀表板

echo.
echo  ============================================
echo   市場監控儀表板
echo  ============================================
echo.
echo   啟動中，瀏覽器會自動開啟 http://127.0.0.1:7788/
echo   每 300 秒自動更新資料
echo.
echo   要停止：在這個視窗按 Ctrl+C
echo   注意：關掉這個視窗，網頁就不會再更新
echo.

python market_monitor_web.py --polymarket --serve

if errorlevel 1 (
    echo.
    echo  ============================================
    echo   [錯誤] 程式異常結束
    echo  ============================================
    echo.
    echo   常見原因：
    echo     1. 缺少套件  ^-^-^>  pip install yfinance pandas requests
    echo     2. SSL 憑證問題  ^-^-^>  python fix_ssl.py
    echo     3. 連接埠 7788 被占用  ^-^-^>  改用 --port 7789
    echo.
    pause
)
