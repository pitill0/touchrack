#!/bin/sh
set -eu

SERVICE_NAME="homelab-touch-console.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this uninstaller with sudo/root." >&2
    exit 1
fi

systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_PATH"
systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

# Restore an interactive tty1 after removing the appliance service.
systemctl start getty@tty1.service 2>/dev/null || true

echo "Removed $SERVICE_NAME and restored getty@tty1.service."
