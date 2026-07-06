@echo off
echo ========================================
echo   CF AUTO ACCOUNT CREATOR - SETUP
echo ========================================
echo.

echo [1] Check Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python NOT FOUND! Download dari https://python.org
    pause
    exit /b 1
)
echo     Python OK

echo.
echo [2] Install websocket-client...
pip install websocket-client
echo.

echo [3] Check ADB...
adb version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ADB NOT FOUND!
    echo     Download: https://dl.google.com/android/repository/platform-tools-latest-windows.zip
    echo     Extract ke folder manapun, lalu tambahkan ke PATH
    pause
    exit /b 1
)
echo     ADB OK

echo.
echo [4] Check device...
adb devices
echo.

echo ========================================
echo   KALAU "List of devices attached" kosong:
echo   1. Buka Settings ^> About Phone
echo   2. Tap "Build Number" 7x sampai Developer Options aktif
echo   3. Settings ^> System ^> Developer Options
echo   4. Enable: "USB Debugging"
echo   5. Colok USB, terima RSA fingerprint di HP
echo   6. Ulangi script ini
echo ========================================
echo.

echo ========================================
echo   PASTIKAN JUGA:
echo   - Kiwi Browser terinstall di HP
echo   - Akun Google daoseed sudah login di Kiwi
echo     (Settings ^> Accounts ^> Add Google Account)
echo ========================================
echo.

pause
