#!/bin/bash
set -euo pipefail

# Safe install into /opt/beatmachine — does not touch other services
APP_DIR="/opt/beatmachine"
REPO="https://github.com/TriphoyFLX/uploader.git"

echo "==> Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git

echo "==> Setting up app in ${APP_DIR}..."
if [ -d "$APP_DIR/.git" ]; then
  cd "$APP_DIR" && git pull origin main
else
  git clone "$REPO" "$APP_DIR"
  cd "$APP_DIR"
fi

echo "==> Python venv..."
python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "==> Creating artist folders..."
    mkdir -p artists/{che,osamason,ninevicious,osamason+che}/{beats,image,visuals}
mkdir -p credentials output

echo "==> Installing systemd service..."
cp deploy/beatmachine.service /etc/systemd/system/beatmachine.service
systemctl daemon-reload
systemctl enable beatmachine

echo ""
echo "DONE. Next steps:"
echo "  1. Copy credentials/token.json and client_secrets.json to ${APP_DIR}/credentials/"
echo "  2. Upload beats to ${APP_DIR}/artists/*/beats/"
echo "  3. Upload images to ${APP_DIR}/artists/*/image/"
echo "  4. systemctl start beatmachine"
echo "  5. systemctl status beatmachine"
echo "  6. tail -f /var/log/beatmachine.log"
