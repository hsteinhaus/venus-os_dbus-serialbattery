#!/bin/sh
#
# Virtual Port Installer for JK-PB via Waveshare RS485-TCP Bridge
# Creates persistent runit services for:
#   - socat virtual serial port
#   - dbus-serialbattery instance bound to that port
#
# Reads configuration from config.ini
# Patches /data/rc.local to activate services at boot
# Idempotent and GitHub-ready
#

APPDIR="/data/apps/virtual-port"
CONFIG="$APPDIR/config.ini"

VERSION=$(head -n 1 "$APPDIR/VERSION")
echo "Virtual Port Manager v$VERSION"

########################################
# 1. Load configuration
########################################
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: $CONFIG not found"
    exit 1
fi

IP=$(grep '^IP=' "$CONFIG" | cut -d= -f2)
PORT=$(grep '^PORT=' "$CONFIG" | cut -d= -f2)
VPORT=$(grep '^VIRTUAL_PORT=' "$CONFIG" | cut -d= -f2)

if [ -z "$IP" ] || [ -z "$PORT" ] || [ -z "$VPORT" ]; then
    echo "ERROR: Missing IP, PORT or VIRTUAL_PORT in config.ini"
    exit 1
fi

echo "Using Waveshare IP: $IP"
echo "Using Waveshare Port: $PORT"
echo "Using Virtual Port: $VPORT"


########################################
# 2. Ensure socat is installed
########################################
if ! command -v socat >/dev/null 2>&1; then
    echo "Installing socat..."
    opkg update
    opkg install socat
else
    echo "socat already installed."
fi


########################################
# 3. Create persistent socat runit service
########################################
echo "Creating persistent socat service..."

mkdir -p /data/etc/runit/socat-ttyV0
mkdir -p /data/etc/runit/socat-ttyV0/log

cat > /data/etc/runit/socat-ttyV0/run << EOF
#!/bin/sh
exec socat pty,link=$VPORT,raw,echo=0 TCP:$IP:$PORT,forever,interval=1
EOF

chmod +x /data/etc/runit/socat-ttyV0/run

cat > /data/etc/runit/socat-ttyV0/log/run << 'EOF'
#!/bin/sh
exec logger -t socat-ttyV0
EOF

chmod +x /data/etc/runit/socat-ttyV0/log/run


########################################
# 4. Create persistent dbus-serialbattery runit service
########################################
echo "Creating persistent dbus-serialbattery service..."

mkdir -p /data/etc/runit/dbus-serialbattery-ttyV0
mkdir -p /data/etc/runit/dbus-serialbattery-ttyV0/log

cat > /data/etc/runit/dbus-serialbattery-ttyV0/run << EOF
#!/bin/sh
exec 2>&1
exec python3 /data/apps/dbus-serialbattery/dbus-serialbattery.py $VPORT
EOF

chmod +x /data/etc/runit/dbus-serialbattery-ttyV0/run

cat > /data/etc/runit/dbus-serialbattery-ttyV0/log/run << 'EOF'
#!/bin/sh
exec logger -t dbus-serialbattery-ttyV0
EOF

chmod +x /data/etc/runit/dbus-serialbattery-ttyV0/log/run

########################################
# 5. Patch /data/rc.local
########################################
echo "Patching /data/rc.local..."

RCLOCAL="/data/rc.local"

# Create rc.local if missing
if [ ! -f "$RCLOCAL" ]; then
    echo "#!/bin/bash" > "$RCLOCAL"
    echo "" >> "$RCLOCAL"
fi

chmod +x "$RCLOCAL"

# Only append block if not already present
if ! grep -q "JK-PB virtual port services" "$RCLOCAL"; then
    cat >> "$RCLOCAL" << 'EOF'

# --- JK-PB virtual port services ---
ln -sf /data/etc/runit/socat-ttyV0 /service/socat-ttyV0
ln -sf /data/etc/runit/dbus-serialbattery-ttyV0 /service/dbus-serialbattery-ttyV0

svc -u /service/socat-ttyV0
svc -u /service/dbus-serialbattery-ttyV0
# --- end JK-PB block ---

EOF
fi

########################################
# 6. Patch rc.local for watchdog service
########################################
if ! grep -q "virtual-port-watchdog" /data/rc.local; then
    echo "Patching /data/rc.local with watchdog service..."

    cat >> /data/rc.local << 'EOF'

# --- Virtual Port Watchdog ---
ln -sf /data/etc/runit/virtual-port-watchdog /service/virtual-port-watchdog
svc -u /service/virtual-port-watchdog
# --- end watchdog block ---
EOF
fi

########################################
# 7. Create watchdog runit service
########################################
echo "Creating watchdog service..."

mkdir -p /data/etc/runit/virtual-port-watchdog
mkdir -p /data/etc/runit/virtual-port-watchdog/log

cp $APPDIR/runit-templates/watchdog/run /data/etc/runit/virtual-port-watchdog/run
cp $APPDIR/runit-templates/watchdog/log/run /data/etc/runit/virtual-port-watchdog/log/run

chmod +x /data/etc/runit/virtual-port-watchdog/run
chmod +x /data/etc/runit/virtual-port-watchdog/log/run

# Enable watchdog at boot
ln -sf /data/etc/runit/virtual-port-watchdog /service/virtual-port-watchdog

# Wait for runit to register the new service
for i in 1 2 3 4 5; do
    if [ -d /service/virtual-port-watchdog/supervise ]; then
        break
    fi
    sleep 1
done

svc -u /service/virtual-port-watchdog


########################################
# 8. Done
########################################
echo "=== Installation complete ==="
echo "Reboot the device to activate services."
