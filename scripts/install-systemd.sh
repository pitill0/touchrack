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
UNIT_BACKUP="$RUNTIME_PARENT/.touchrack-unit.$$"
INSTALL_LOCK="$RUNTIME_PARENT/.touchrack-install.lock"

# Preserve the previous runtime until installation is committed.
DEPLOYMENT_STARTED=0
DEPLOYMENT_COMMITTED=0
HAD_RUNTIME=0
HAD_SERVICE_UNIT=0
UNIT_REPLACEMENT_STARTED=0
LOCK_ACQUIRED=0
BUILD_OWNED=0

rollback_runtime() {
    if [ -d "$OLD_DIR" ]; then
        rm -rf "$RUNTIME_DIR" || return 1
        mv "$OLD_DIR" "$RUNTIME_DIR" || return 1
    elif [ "$HAD_RUNTIME" -eq 0 ]; then
        # First installation: discard an incomplete runtime.
        rm -rf "$RUNTIME_DIR" || return 1
    fi
}

rollback_service_unit() {
    if [ "$UNIT_REPLACEMENT_STARTED" -eq 0 ]; then
        return 0
    fi

    if [ "$HAD_SERVICE_UNIT" -eq 1 ]; then
        # Retain the backup if restoring the unit fails.
        cp -p "$UNIT_BACKUP" "$SERVICE_PATH" || return 1
    else
        # The unit did not exist before this installation.
        rm -f "$SERVICE_PATH" || return 1
    fi

    # Refresh systemd's view after restoring the previous unit.
    systemctl daemon-reload || return 1
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM

    if [ "$DEPLOYMENT_STARTED" -eq 1 ] &&
       [ "$DEPLOYMENT_COMMITTED" -eq 0 ]; then
        if ! rollback_runtime; then
            echo "ERROR: rollback failed; inspect $OLD_DIR" >&2
            status=1
        fi
    fi

    # Restore the systemd unit as part of the same transaction.
    if [ "$DEPLOYMENT_STARTED" -eq 1 ] &&
       [ "$DEPLOYMENT_COMMITTED" -eq 0 ]; then
        if ! rollback_service_unit; then
            echo "ERROR: systemd unit rollback failed; inspect $UNIT_BACKUP" >&2
            status=1
        else
            rm -f "$UNIT_BACKUP" || status=1
        fi
    fi

    # Never remove a build directory owned by another process.
    if [ "$BUILD_OWNED" -eq 1 ]; then
        if ! rm -rf "$BUILD_DIR"; then
            status=1
        fi
    fi

    # Only the process that acquired the lock may release it.
    if [ "$LOCK_ACQUIRED" -eq 1 ]; then
        if ! rmdir "$INSTALL_LOCK"; then
            echo "ERROR: could not release install lock: $INSTALL_LOCK" >&2
            status=1
        fi
    fi

    exit "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# The lock covers the service check and the entire deployment.
install -d -o root -g root -m 0755 "$RUNTIME_PARENT"

if ! mkdir "$INSTALL_LOCK"; then
    echo "ERROR: installation already locked: $INSTALL_LOCK" >&2
    echo "Inspect an existing/stale lock before retrying." >&2
    exit 1
fi
LOCK_ACQUIRED=1

# A prior run may have been killed without executing EXIT cleanup.
# Do not deploy over a potentially incomplete or unrecovered installation.
# The lock is held while checking these artifact names.
for recovery_artifact in \
    "$RUNTIME_PARENT"/.touchrack-old.* \
    "$RUNTIME_PARENT"/.touchrack-unit.* \
    "$RUNTIME_PARENT"/.touchrack-build.*
do
    if [ -e "$recovery_artifact" ] || [ -L "$recovery_artifact" ]; then
        echo "ERROR: unresolved previous installation artifact: $recovery_artifact" >&2
        echo "Inspect and recover it manually before retrying." >&2
        exit 1
    fi
done

# Never destroy recovery artifacts from a previous interrupted run.
if [ -e "$BUILD_DIR" ] || [ -L "$BUILD_DIR" ]; then
    echo "ERROR: existing build artifact: $BUILD_DIR" >&2
    exit 1
fi

if [ -e "$OLD_DIR" ] || [ -L "$OLD_DIR" ]; then
    echo "ERROR: existing runtime backup: $OLD_DIR" >&2
    exit 1
fi

if [ -e "$UNIT_BACKUP" ] || [ -L "$UNIT_BACKUP" ]; then
    echo "ERROR: existing systemd backup: $UNIT_BACKUP" >&2
    exit 1
fi

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "ERROR: $SERVICE_NAME is running." >&2
    echo "Stop it before deploying a new runtime:" >&2
    echo "  sudo systemctl stop $SERVICE_NAME" >&2
    exit 1
fi

# The lock is held and all artifact paths were checked.
BUILD_OWNED=1
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

# Enter the transaction before moving the previous runtime.
if [ -d "$RUNTIME_DIR" ]; then
    HAD_RUNTIME=1
fi

DEPLOYMENT_STARTED=1

if [ "$HAD_RUNTIME" -eq 1 ]; then
    mv "$RUNTIME_DIR" "$OLD_DIR"
fi

if ! install -d -o root -g root -m 0755 "$RUNTIME_DIR"; then
    exit 1
fi

# Important: create the venv at its final path. Python entry points embed
# an absolute interpreter path in their shebang and are not safely movable.
if ! "$PYTHON_BIN" -m venv "$RUNTIME_DIR/venv"; then
    exit 1
fi

if ! "$RUNTIME_DIR/venv/bin/python" -m pip install --upgrade pip; then
    exit 1
fi

if ! "$RUNTIME_DIR/venv/bin/pip" install "$WHEEL_PATH"; then
    exit 1
fi

if [ ! -x "$RUNTIME_DIR/venv/bin/homelab-console" ]; then
    echo "ERROR: homelab-console entry point was not created." >&2
    exit 1
fi

EXPECTED_SHEBANG="#!$RUNTIME_DIR/venv/bin/python"
ACTUAL_SHEBANG=$(sed -n '1p' "$RUNTIME_DIR/venv/bin/homelab-console")
if [ "$ACTUAL_SHEBANG" != "$EXPECTED_SHEBANG" ]; then
    echo "ERROR: invalid homelab-console shebang: $ACTUAL_SHEBANG" >&2
    exit 1
fi

# Configuration and systemd setup remain inside the transaction.
install -d -o root -g root -m 0755 "$CONFIG_DIR"
copy_initial_config "$CONFIG_DIR/config.yaml" "config.yaml" "config.example.yaml"
copy_initial_config "$CONFIG_DIR/services.yaml" "services.yaml" "services.example.yaml"

RUNTIME_ESCAPED=$(escape_sed "$RUNTIME_DIR")
VENV_ESCAPED=$(escape_sed "$RUNTIME_DIR/venv/bin")
CONFIG_ESCAPED=$(escape_sed "$CONFIG_DIR")

# Preserve the original unit before writing a replacement.
if [ -f "$SERVICE_PATH" ]; then
    HAD_SERVICE_UNIT=1
    cp -p "$SERVICE_PATH" "$UNIT_BACKUP"
fi

# From this point, any error must restore the previous unit too.
UNIT_REPLACEMENT_STARTED=1

sed \
    -e "s|@RUNTIME_DIR@|$RUNTIME_ESCAPED|g" \
    -e "s|@VENV_BIN@|$VENV_ESCAPED|g" \
    -e "s|@CONFIG_DIR@|$CONFIG_ESCAPED|g" \
    "$TEMPLATE" > "$SERVICE_PATH"
chown root:root "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

systemctl daemon-reload

# No rollback after this point: the new runtime is ready.
DEPLOYMENT_COMMITTED=1
rm -rf "$OLD_DIR"
rm -f "$UNIT_BACKUP"

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
