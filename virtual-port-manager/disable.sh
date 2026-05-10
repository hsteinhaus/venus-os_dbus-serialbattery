#!/bin/sh

echo "Disabling Virtual Port Manager services..."

# Stop services cleanly
svc -d /service/socat-ttyV0 2>/dev/null
svc -d /service/dbus-serialbattery-ttyV0 2>/dev/null
svc -d /service/virtual-port-watchdog 2>/dev/null

sleep 1

# Remove symlinks so they don't restart
rm -f /service/socat-ttyV0
rm -f /service/dbus-serialbattery-ttyV0
rm -f /service/virtual-port-watchdog

echo "Services disabled."
