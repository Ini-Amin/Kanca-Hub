@echo off
echo Auto-FreeCF - Cloudflare Workers AI Token Grabber
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found!
    echo Install: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Auto setup venv
if not exist "venv" (
    echo Setting up virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Auto install dependencies
if not exist "venv\.installed" (
    echo Installing dependencies...
    pip install -q -r requirements.txt
    playwright install chromium
    echo. > venv\.installed
    echo Setup complete!
)

REM Run based on arguments
if "%1"=="--web" (
    echo Starting Web UI on http://localhost:8080
    python web_ui.py --port 8080
) else if "%1"=="--tui" (
    echo Starting Terminal UI...
    python terminal_ui.py
) else if "%1"=="--accounts" (
    echo Processing accounts...
    python browser_bot.py --accounts %2
) else (
    echo.
    echo Usage:
    echo   run.bat --web              Web UI (browser)
    echo   run.bat --tui              Terminal UI
    echo   run.bat --accounts FILE    Process accounts file
    echo.
    echo Example:
    echo   run.bat --web
    echo   run.bat --accounts accounts.json
)
