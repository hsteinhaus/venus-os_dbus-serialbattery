#!/bin/sh

APPDIR="/data/apps/virtual-port"
CONFIG="$APPDIR/config.ini"
VERSIONFILE="$APPDIR/VERSION"

echo "=== Virtual Port Manager Status ==="

########################################
# Version
########################################
if [ -f "$VERSIONFILE" ]; then
    VERSION=$(sed -n '1p' "$VERSIONFILE")
    DATE=$(sed -n '2p' "$VERSIONFILE")
    DESC=$(sed -n '3p' "$VERSIONFILE")
    echo "Version: $VERSION ($DATE)"
    echo "Description: $DESC"
else
    echo "Version: unknown"
fi

echo ""

########################################
# Load config
########################################
IP=$(grep '^IP=' "$CONFIG" | cut -d= -f2)
PORT=$(grep '^PORT=' "$CONFIG" | cut -d= -f2)
VPORT=$(grep '^VIRTUAL_PORT=' "$CONFIG" | cut -d= -f2)

echo "Configured Waveshare: $IP:$PORT"
echo "Virtual Port: $VPORT"
echo ""

########################################
# PTY status
########################################
if [ -e "$VPORT" ]; then
    echo "PTY: OK ($VPORT exists)"
else
    echo "PTY: MISSING ($VPORT does not exist)"
fi

echo ""

########################################
# socat status
########################################
if pgrep -f "socat.*$VPORT" >/dev/null; then
    echo "socat: RUNNING"
else
    echo "socat: NOT RUNNING"
fi

echo ""

########################################
# dbus-serialbattery status
########################################
if pgrep -f "dbus-serialbattery.*$VPORT" >/dev/null; then
    echo "dbus-serialbattery: RUNNING"
else
    echo "dbus-serialbattery: NOT RUNNING"
fi

echo ""

########################################
# watchdog status
########################################
if pgrep -f "virtual-port-watchdog" >/dev/null; then
    echo "watchdog: RUNNING"
else
    echo "watchdog: NOT RUNNING"
fi

echo ""

########################################
# Waveshare TCP reachability test
########################################
echo "Testing Waveshare TCP connection..."

# If socat is connected, TCP is definitely OK
if pgrep -f "socat.*$IP:$PORT" >/dev/null; then
    echo "TCP: OK (primary connection active)"
else
    # Only test direct TCP if socat is NOT connected
    if timeout 2 sh -c "echo > /dev/tcp/$IP/$PORT" 2>/dev/null; then
        echo "TCP: OK (reachable)"
    else
        echo "TCP: FAILED (not reachable)"
    fi
fi

echo ""

########################################
# Last 10 watchdog log entries
########################################
echo "Last 10 watchdog log entries:"
if [ -f /var/log/virtual-port-watchdog.log ]; then
    tail -n 10 /var/log/virtual-port-watchdog.log
else
    echo "(no log file yet)"
fi

echo "=== Status check complete ==="
