#!/bin/bash
# Installs the always-on radio listener on an Ubuntu server (Oracle Cloud Always Free).
# Run: curl -fsSL https://raw.githubusercontent.com/zouga31400/listening-notes/main/deploy/oracle/install.sh | sudo bash
# Safe to run again (it updates what is already installed).
set -euo pipefail
REPO=${REPO:-https://github.com/zouga31400/listening-notes.git}
BRANCH=${BRANCH:-main}
BASE=/opt/listening-notes
APP=$BASE/app
ENV_DIR=/etc/listening-notes

echo "== System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git ffmpeg python3-venv python3-pip >/dev/null
timedatectl set-timezone Europe/Paris || true

# Small machines (1 GB): add swap so that the audio analysis never runs out of memory.
if [ "$(free -m | awk '/Mem:/ {print $2}')" -lt 2000 ] && ! swapon --show | grep -q /swapfile; then
  echo "== Adding 2 GB of swap"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Service account and code"
id radio >/dev/null 2>&1 || useradd --system --home "$BASE" --shell /usr/sbin/nologin radio
mkdir -p "$BASE" && chown radio:radio "$BASE"
if [ -d "$APP/.git" ]; then
  sudo -u radio git -C "$APP" pull --ff-only --quiet
else
  sudo -u radio git clone --quiet --branch "$BRANCH" "$REPO" "$APP"
fi

echo "== Python packages (a few minutes the first time)"
[ -x "$BASE/venv/bin/python" ] || sudo -u radio python3 -m venv "$BASE/venv"
sudo -u radio "$BASE/venv/bin/pip" install -q --upgrade pip
sudo -u radio "$BASE/venv/bin/pip" install -q -r "$APP/deploy/oracle/requirements.txt"

echo "== Settings file"
mkdir -p "$ENV_DIR" && chmod 700 "$ENV_DIR"
if [ ! -f "$ENV_DIR/radio.env" ]; then
  cp "$APP/deploy/oracle/radio.env.example" "$ENV_DIR/radio.env"
  chmod 600 "$ENV_DIR/radio.env"
  FRESH=1
fi

echo "== Services"
chmod +x "$APP/deploy/oracle/update.sh"
cp "$APP/deploy/oracle/radio-offshow.service" "$APP/deploy/oracle/radio-update.service" \
   "$APP/deploy/oracle/radio-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet radio-offshow radio-update.timer
systemctl start radio-update.timer

if [ "${FRESH:-}" = "1" ] || ! grep -q '^STREAM_URL=.' "$ENV_DIR/radio.env"; then
  echo
  echo "Installed. Now fill in the settings (same values as the GitHub secrets):"
  echo "  sudo nano $ENV_DIR/radio.env"
  echo "then start the listener:"
  echo "  sudo systemctl restart radio-offshow"
else
  systemctl restart radio-offshow
  echo "Installed and (re)started."
fi
echo "Logs: sudo journalctl -u radio-offshow -f"
