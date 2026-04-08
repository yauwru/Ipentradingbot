#!/bin/bash
# ============================================================
# Capitol Trades Copy-Trading Bot — Deployment Script
# Target: Ubuntu 24.04 VPS
# Usage:  bash deploy.sh
# ============================================================
set -e

REPO_URL="https://github.com/yauwru/ipentradingbot.git"
BRANCH="claude/copy-trading-bot-setup-3jFRk"
INSTALL_DIR="/opt/ipentradingbot"
SERVICE_NAME="copy-trading-bot"
PYTHON="python3"

echo ""
echo "============================================"
echo "  Capitol Trades Copy-Trading Bot Setup"
echo "============================================"
echo ""

# ── 1. System dependencies ──────────────────────────────────
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# ── 2. Clone / update repo ──────────────────────────────────
echo "[2/6] Cloning repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Repo already exists, pulling latest..."
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ── 3. Python virtual environment + dependencies ────────────
echo "[3/6] Setting up Python virtual environment..."
$PYTHON -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
echo "  Dependencies installed."

# ── 4. Write .env file ──────────────────────────────────────
echo "[4/6] Writing .env configuration..."
cat > "$INSTALL_DIR/.env" << 'ENVEOF'
ALPACA_API_KEY=PKJ6UBSTVIVGCGUC5KUWLHMZLV
ALPACA_SECRET_KEY=E7y7ktEjQTZ8CvWeJKpzWJATsNpwizYCkLLBAbbRjt47
ALPACA_BASE_URL=https://paper-api.alpaca.markets
POSITION_SIZE_USD=1000
MAX_POSITIONS=20
SCRAPE_INTERVAL_MINUTES=60
COPY_OPTIONS=false
MIN_TRADE_AMOUNT=1000
ENVEOF
chmod 600 "$INSTALL_DIR/.env"
echo "  .env written (permissions: 600)"

# ── 5. Create logs directory ────────────────────────────────
mkdir -p "$INSTALL_DIR/copy_trading_bot/logs"

# ── 6. Install systemd service ──────────────────────────────
echo "[5/6] Installing systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SERVICEEOF
[Unit]
Description=Capitol Trades Copy-Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo ""
echo "[6/6] Verifying service status..."
sleep 3
systemctl status "$SERVICE_NAME" --no-pager -l

echo ""
echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Useful commands:"
echo "  - View live logs : journalctl -u $SERVICE_NAME -f"
echo "  - Stop bot       : systemctl stop $SERVICE_NAME"
echo "  - Start bot      : systemctl start $SERVICE_NAME"
echo "  - Check status   : systemctl status $SERVICE_NAME"
echo "  - Trade history  : cd $INSTALL_DIR && .venv/bin/python main.py --history"
echo "  - Portfolio      : cd $INSTALL_DIR && .venv/bin/python main.py --portfolio"
echo "============================================"
echo ""
