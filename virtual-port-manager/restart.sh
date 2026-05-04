#!/bin/sh

# Restart one or all dbus-serialbattery virtual-port instances
# Usage:
#   restart-virtual-ports.sh        → restart channel 0
#   restart-virtual-ports.sh 0      → restart channel 0
#   restart-virtual-ports.sh all    → restart all channels
#   restart-virtual-ports.sh 0 1 2  → restart channels 0,1,2

CHANNELS="$@"

# Default to channel 0 if no args
if [ -z "$CHANNELS" ]; then
    CHANNELS="0"
fi

restart_channel() {
    CH="$1"
    SERVICE="/service/dbus-serialbattery-ttyV${CH}"

    echo "Restarting dbus-serialbattery for virtual port channel ${CH}..."

    # Disable + stop
    svc -dx "$SERVICE"
    sleep 0.5

    # Wait for clean exit
    TIMEOUT=50
    COUNT=0
    while pgrep -f "dbus-serialbattery.*ttyV${CH}" >/dev/null; do
        sleep 0.2
        COUNT=$((COUNT+1))
        if [ "$COUNT" -ge "$TIMEOUT" ]; then
            echo "WARNING: channel ${CH} did not stop cleanly, forcing kill..."
            pkill -f "dbus-serialbattery.*ttyV${CH}"
            break
        fi
    done

    # Re-enable + start
    svc -u "$SERVICE"
    sleep 1

    # Confirm
    if pgrep -f "dbus-serialbattery.*ttyV${CH}" >/dev/null; then
        echo "Channel ${CH}: restarted successfully."
    else
        echo "Channel ${CH}: ERROR — failed to start."
    fi

    echo ""
}

# Expand "all" into a list of channels
if [ "$CHANNELS" = "all" ]; then
    CHANNELS="0 1 2 3"
fi

# Restart each channel
for CH in $CHANNELS; do
    restart_channel "$CH"
done
