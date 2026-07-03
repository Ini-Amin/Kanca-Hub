#!/bin/bash
set -e

# Auto-FreeCF Runner - Auto Setup & Run

echo "🚀 Auto-FreeCF - Cloudflare Workers AI Token Grabber"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python not found!"
    echo "Install: https://www.python.org/downloads/"
    exit 1
fi

# Auto setup venv
if [ ! -d "venv" ]; then
    echo "📦 Setting up virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Auto install dependencies
if [ ! -f "venv/.installed" ]; then
    echo "📦 Installing dependencies..."
    pip install -q -r requirements.txt
    playwright install chromium
    touch venv/.installed
    echo "✅ Setup complete!"
fi

# Run based on arguments
if [ "$1" = "--web" ]; then
    echo "🌐 Starting Web UI on http://localhost:8080"
    python web_ui.py --port 8080
elif [ "$1" = "--tui" ]; then
    echo "💻 Starting Terminal UI..."
    python terminal_ui.py
elif [ "$1" = "--accounts" ]; then
    echo "📝 Processing accounts..."
    python browser_bot.py --accounts "$2"
else
    echo ""
    echo "Usage:"
    echo "  ./run.sh --web              # Web UI (browser)"
    echo "  ./run.sh --tui              # Terminal UI"
    echo "  ./run.sh --accounts FILE    # Process accounts file"
    echo ""
    echo "Example:"
    echo "  ./run.sh --web"
    echo "  ./run.sh --accounts accounts.json"
fi
