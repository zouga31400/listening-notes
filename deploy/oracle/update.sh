#!/bin/bash
# Pulls the latest code from GitHub; restarts the listener only when something changed.
set -euo pipefail
APP=/opt/listening-notes/app
before=$(sudo -u radio git -C "$APP" rev-parse HEAD)
sudo -u radio git -C "$APP" pull --ff-only --quiet
after=$(sudo -u radio git -C "$APP" rev-parse HEAD)
if [ "$before" != "$after" ]; then
  if ! sudo -u radio git -C "$APP" diff --quiet "$before" "$after" -- deploy/oracle/requirements.txt; then
    sudo -u radio /opt/listening-notes/venv/bin/pip install -q -r "$APP/deploy/oracle/requirements.txt"
  fi
  systemctl restart radio-offshow
  echo "Updated to $after, listener restarted"
else
  echo "Already up to date"
fi
