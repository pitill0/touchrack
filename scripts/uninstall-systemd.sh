#!/bin/sh
set -eu

SERVICE_NAME="homelab-touch-console.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
RUNTIME_DIR="/opt/touchrack"
CONFIG_DIR="/etc/touchrack"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this uninstaller with sudo/root." >&2
    exit 1
fi

systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_PATH"
rm -rf "$RUNTIME_DIR"
systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

systemctl start getty@tty1.service 2>/dev/null || true

echo "Removed $SERVICE_NAME and runtime $RUNTIME_DIR."
echo "Preserved configuration in $CONFIG_DIR."
echo "Restored getty@tty1.service."
