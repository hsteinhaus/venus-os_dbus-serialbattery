#!/bin/sh
#
# Uninstaller for JK-PB Virtual Port Manager
# Removes:
#   - socat-ttyV0 persistent runit service
#   - dbus-serialbattery-ttyV0 persistent runit service
#   - symlinks under /service
#   - rc.local block added by installer
#
# Leaves:
#   - /data/apps/virtual-port/ folder
#   - config.ini
#   - dbus-serialbattery installation
#

VERSION=$(head -n 1 "$APPDIR/VERSION")
echo "Virtual Port Manager v$VERSION"

########################################
# 1. Stop and remove symlinks under /service
########################################
echo "Stopping services (if running)..."

svc -d /service/socat-ttyV0 2>/dev/null
svc -d /service/dbus-serialbattery-ttyV0 2>/dev/null

echo "Removing /service symlinks..."
rm -f /service/socat-ttyV0
rm -f /service/dbus-serialbattery-ttyV0


########################################
# 2. Remove persistent runit service directories
########################################
echo "Removing persistent runit services..."

rm -rf /data/etc/runit/socat-ttyV0
rm -rf /data/etc/runit/dbus-serialbattery-ttyV0

########################################
# 3. Remove rc.local blocks (marker-based)
########################################
RCLOCAL="/data/rc.local"

echo "Cleaning up /data/rc.local..."

if [ -f "$RCLOCAL" ]; then
    # Remove JK-PB block
    sed -i '/# --- JK-PB virtual port services ---/,/# --- end JK-PB block ---/d' "$RCLOCAL"

    # Remove Virtual Port Watchdog block
    sed -i '/# --- Virtual Port Watchdog ---/,/# --- end watchdog block ---/d' "$RCLOCAL"
fi

########################################
# 4. Remove watchdog service
########################################
echo "Removing watchdog service..."

svc -d /service/virtual-port-watchdog 2>/dev/null
rm -f /service/virtual-port-watchdog
rm -rf /data/etc/runit/virtual-port-watchdog

########################################
# 5. Done
########################################
echo "=== Uninstallation complete ==="
echo "You may reboot to ensure all services are fully removed."
