#!/bin/sh
set -eu

SERVICE_NAME="homelab-touch-console.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this installer with sudo/root." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TEMPLATE="$PROJECT_DIR/systemd/homelab-touch-console.service.in"
VENV_BIN="$PROJECT_DIR/.venv/bin"

if [ ! -x "$VENV_BIN/homelab-console" ]; then
    echo "ERROR: $VENV_BIN/homelab-console not found or not executable." >&2
    echo "Install the project into its .venv first: .venv/bin/pip install -e ." >&2
    exit 1
fi

if [ ! -x /usr/bin/openvt ]; then
    echo "ERROR: /usr/bin/openvt is required." >&2
    exit 1
fi

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

PROJECT_ESCAPED=$(escape_sed "$PROJECT_DIR")
VENV_ESCAPED=$(escape_sed "$VENV_BIN")

sed \
    -e "s|@PROJECT_DIR@|$PROJECT_ESCAPED|g" \
    -e "s|@VENV_BIN@|$VENV_ESCAPED|g" \
    "$TEMPLATE" > "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

systemctl daemon-reload

echo "Installed: $SERVICE_PATH"
echo "Project:   $PROJECT_DIR"
echo
echo "Start now:"
echo "  sudo systemctl start $SERVICE_NAME"
echo
echo "Enable at boot and start now:"
echo "  sudo systemctl enable --now $SERVICE_NAME"
echo
echo "Status/logs:"
echo "  systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -b"
