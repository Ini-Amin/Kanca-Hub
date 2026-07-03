@echo off
setlocal enabledelayedexpansion

REM Logo
echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║                                                          ║
echo ║   🚀 Auto-FreeCF                                         ║
echo ║   Cloudflare Workers AI Account ID ^& Token Grabber       ║
echo ║                                                          ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python not found!
    echo Install: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Auto setup venv
if not exist "venv" (
    echo 📦 Setting up virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Auto install dependencies
if not exist "venv\.installed" (
    echo 📦 Installing dependencies...
    pip install -q -r requirements.txt
    playwright install chromium
    echo. > venv\.installed
    echo ✅ Setup complete!
    echo.
)

REM Interactive menu
echo Choose an option:
echo.
echo   [1] 🌐 Web UI (browser interface)
echo   [2] 💻 Terminal UI (interactive menu)
echo   [3] 📝 Process accounts file
echo   [4] 🚪 Exit
echo.
set /p choice="Select option (1-4): "

if "%choice%"=="1" (
    echo.
    echo 🌐 Starting Web UI on http://localhost:8080
    python web_ui.py --port 8080
) else if "%choice%"=="2" (
    echo.
    echo 💻 Starting Terminal UI...
    python terminal_ui.py
) else if "%choice%"=="3" (
    echo.
    set /p filepath="Enter accounts file path (default: accounts.json): "
    if "!filepath!"=="" set filepath=accounts.json
    echo 📝 Processing accounts from !filepath!...
    python browser_bot.py --accounts !filepath!
) else if "%choice%"=="4" (
    echo.
    echo Goodbye! 👋
    exit /b 0
) else (
    echo.
    echo ❌ Invalid option
    exit /b 1
)
