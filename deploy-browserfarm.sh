#!/bin/bash
# Deploy Auto-FreeCF signup pipeline to Browser Farm VPS
# Usage: ./deploy-browserfarm.sh

set -e

VPS_IP="172.232.228.117"
SSH_KEY="$HOME/.ssh/id_ed25519"
VPS_USER="root"
VPS_DIR="/root/bluk-cf"
ADAPTER_DIR="/root/mail-adapter"

echo "🚀 Deploying to Browser Farm VPS: $VPS_IP"

# 1. Deploy signup pipeline
echo "📦 Deploying signup pipeline..."
rsync -avz -e "ssh -i $SSH_KEY" \
    --exclude '__pycache__' \
    --exclude 'results.json' \
    --exclude '.git' \
    signup_from_scratch/ \
    "$VPS_USER@$VPS_IP:$VPS_DIR/"

# 2. Deploy mail adapter
echo "📧 Deploying mail adapter..."
rsync -avz -e "ssh -i $SSH_KEY" \
    mail-adapter/ \
    "$VPS_USER@$VPS_IP:$ADAPTER_DIR/"

# 3. Install deps
echo "📥 Installing dependencies..."
ssh -i "$SSH_KEY" "$VPS_USER@$VPS_IP" "
    cd $VPS_DIR && pip install -r requirements.txt 2>/dev/null || true
    cd $ADAPTER_DIR && pip install -r requirements.txt 2>/dev/null || true
"

# 4. Setup config
echo "⚙️  Setting up config..."
ssh -i "$SSH_KEY" "$VPS_USER@$VPS_IP" "
    cd $VPS_DIR
    if [ ! -f config.json ]; then
        cp config.example.json config.json
        echo 'Created config.json from example'
    else
        echo 'config.json already exists, skipped'
    fi
    
    cd $ADAPTER_DIR
    if [ ! -f config.json ]; then
        cp config.example.json config.json
        echo 'Created adapter config.json from example'
    else
        echo 'Adapter config.json already exists, skipped'
    fi
"

# 5. Start services
echo "🔧 Starting services..."
ssh -i "$SSH_KEY" "$VPS_USER@$VPS_IP" "
    # Start Xvfb if not running
    if ! pgrep -f 'Xvfb :99' > /dev/null; then
        Xvfb :99 -screen 0 1920x1080x24 &>/dev/null &
        echo 'Started Xvfb :99'
    fi
    
    # Start mail adapter if not running
    if ! pgrep -f 'adapter.py' > /dev/null; then
        cd $ADAPTER_DIR && nohup python3 adapter.py &>/dev/null &
        echo 'Started mail adapter on :9877'
    else
        echo 'Mail adapter already running'
    fi
"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Next steps:"
echo "  1. Edit config.json on VPS: ssh -i $SSH_KEY root@$VPS_IP 'nano $VPS_DIR/config.json'"
echo "  2. Run signup: ssh -i $SSH_KEY root@$VPS_IP 'cd $VPS_DIR && DISPLAY=:99 python3 main.py'"
echo "  3. Run with accounts: ssh -i $SSH_KEY root@$VPS_IP 'cd $VPS_DIR && DISPLAY=:99 python3 main.py -n 5'"
