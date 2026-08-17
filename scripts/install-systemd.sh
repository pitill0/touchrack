#!/bin/sh
set -eu

SERVICE_NAME="homelab-touch-console.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
RUNTIME_DIR="/opt/touchrack"
CONFIG_DIR="/etc/touchrack"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this installer with sudo/root." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TEMPLATE="$PROJECT_DIR/systemd/homelab-touch-console.service.in"
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}

if [ ! -r "$TEMPLATE" ]; then
    echo "ERROR: systemd template not found: $TEMPLATE" >&2
    exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: Python interpreter not found: $PYTHON_BIN" >&2
    exit 1
fi

if [ ! -x /usr/bin/openvt ]; then
    echo "ERROR: /usr/bin/openvt is required." >&2
    exit 1
fi

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    echo "ERROR: Python venv support is required (Ubuntu: python3-venv)." >&2
    exit 1
fi

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

copy_initial_config() {
    destination=$1
    local_name=$2
    example_name=$3

    if [ -e "$destination" ]; then
        return
    fi

    if [ -f "$PROJECT_DIR/$local_name" ]; then
        install -o root -g root -m 0644 \
            "$PROJECT_DIR/$local_name" "$destination"
        echo "Migrated configuration: $destination"
    elif [ -f "$PROJECT_DIR/$example_name" ]; then
        install -o root -g root -m 0644 \
            "$PROJECT_DIR/$example_name" "$destination"
        echo "Installed example configuration: $destination"
    fi
}

RUNTIME_PARENT=$(dirname "$RUNTIME_DIR")
BUILD_DIR="$RUNTIME_PARENT/.touchrack-build.$$"
OLD_DIR="$RUNTIME_PARENT/.touchrack-old.$$"

cleanup() {
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT HUP INT TERM

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "ERROR: $SERVICE_NAME is running." >&2
    echo "Stop it before deploying a new runtime:" >&2
    echo "  sudo systemctl stop $SERVICE_NAME" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "$RUNTIME_PARENT"
rm -rf "$BUILD_DIR" "$OLD_DIR"
install -d -o root -g root -m 0755 "$BUILD_DIR"

# The system interpreter is not required to have a global pip installation.
# Build the wheel inside a disposable venv instead.
BUILD_VENV="$BUILD_DIR/venv"
echo "Creating isolated build environment in $BUILD_VENV ..."
if ! "$PYTHON_BIN" -m venv "$BUILD_VENV"; then
    echo "ERROR: could not create build venv (Ubuntu: install python3-venv)." >&2
    exit 1
fi

if ! "$BUILD_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "ERROR: build venv was created without pip." >&2
    exit 1
fi

WHEEL_DIR="$BUILD_DIR/wheel"
install -d -o root -g root -m 0755 "$WHEEL_DIR"

echo "Building TouchRack wheel in $WHEEL_DIR ..."
"$BUILD_VENV/bin/python" -m pip wheel \
    --no-deps \
    --wheel-dir "$WHEEL_DIR" \
    "$PROJECT_DIR"

set -- "$WHEEL_DIR"/touchrack-*.whl
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "ERROR: expected exactly one TouchRack wheel in $WHEEL_DIR." >&2
    exit 1
fi
WHEEL_PATH=$1

if [ -d "$RUNTIME_DIR" ]; then
    mv "$RUNTIME_DIR" "$OLD_DIR"
fi

rollback_runtime() {
    rm -rf "$RUNTIME_DIR"
    if [ -d "$OLD_DIR" ]; then
        mv "$OLD_DIR" "$RUNTIME_DIR"
    fi
}

if ! install -d -o root -g root -m 0755 "$RUNTIME_DIR"; then
    rollback_runtime
    exit 1
fi

# Important: create the venv at its final path. Python entry points embed
# an absolute interpreter path in their shebang and are not safely movable.
if ! "$PYTHON_BIN" -m venv "$RUNTIME_DIR/venv"; then
    rollback_runtime
    exit 1
fi

if ! "$RUNTIME_DIR/venv/bin/python" -m pip install --upgrade pip; then
    rollback_runtime
    exit 1
fi

if ! "$RUNTIME_DIR/venv/bin/pip" install "$WHEEL_PATH"; then
    rollback_runtime
    exit 1
fi

if [ ! -x "$RUNTIME_DIR/venv/bin/homelab-console" ]; then
    echo "ERROR: homelab-console entry point was not created." >&2
    rollback_runtime
    exit 1
fi

EXPECTED_SHEBANG="#!$RUNTIME_DIR/venv/bin/python"
ACTUAL_SHEBANG=$(sed -n '1p' "$RUNTIME_DIR/venv/bin/homelab-console")
if [ "$ACTUAL_SHEBANG" != "$EXPECTED_SHEBANG" ]; then
    echo "ERROR: invalid homelab-console shebang: $ACTUAL_SHEBANG" >&2
    rollback_runtime
    exit 1
fi

rm -rf "$OLD_DIR"
rm -rf "$BUILD_DIR"
trap - EXIT HUP INT TERM

install -d -o root -g root -m 0755 "$CONFIG_DIR"
copy_initial_config "$CONFIG_DIR/config.yaml" "config.yaml" "config.example.yaml"
copy_initial_config "$CONFIG_DIR/services.yaml" "services.yaml" "services.example.yaml"

RUNTIME_ESCAPED=$(escape_sed "$RUNTIME_DIR")
VENV_ESCAPED=$(escape_sed "$RUNTIME_DIR/venv/bin")
CONFIG_ESCAPED=$(escape_sed "$CONFIG_DIR")

sed \
    -e "s|@RUNTIME_DIR@|$RUNTIME_ESCAPED|g" \
    -e "s|@VENV_BIN@|$VENV_ESCAPED|g" \
    -e "s|@CONFIG_DIR@|$CONFIG_ESCAPED|g" \
    "$TEMPLATE" > "$SERVICE_PATH"
chown root:root "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

systemctl daemon-reload

echo
echo "Installed root-owned TouchRack runtime:"
echo "  $RUNTIME_DIR"
echo "Configuration (preserved across updates/uninstall):"
echo "  $CONFIG_DIR"
echo "Installed systemd unit:"
echo "  $SERVICE_PATH"
echo
echo "The installer does not restart an already running service automatically."
echo "Activate this deployment with:"
echo "  sudo systemctl restart $SERVICE_NAME"
echo
echo "Enable at boot and start now:"
echo "  sudo systemctl enable --now $SERVICE_NAME"
echo
echo "Status/logs:"
echo "  systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -b"
