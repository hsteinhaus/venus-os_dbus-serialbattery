#!/bin/sh

echo "Enabling Virtual Port Manager services..."

ln -sf /data/etc/runit/socat-ttyV0 /service/socat-ttyV0
ln -sf /data/etc/runit/dbus-serialbattery-ttyV0 /service/dbus-serialbattery-ttyV0
ln -sf /data/etc/runit/virtual-port-watchdog /service/virtual-port-watchdog

# Give runit a moment to create supervise dirs
sleep 1

svc -u /service/socat-ttyV0
svc -u /service/dbus-serialbattery-ttyV0
svc -u /service/virtual-port-watchdog

echo "Services enabled."

