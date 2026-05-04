#!/bin/sh

CHANNEL="${1:-0}"
SERVICE="/service/socat-ttyV${CHANNEL}"

echo "Restarting socat for virtual port channel ${CHANNEL}..."

# Disable + stop
svc -dx "$SERVICE"
sleep 0.5

# Wait for clean exit
TIMEOUT=50
COUNT=0
while pgrep -f "socat.*ttyV${CHANNEL}" >/dev/null; do
    sleep 0.2
    COUNT=$((COUNT+1))
    if [ "$COUNT" -ge "$TIMEOUT" ]; then
        echo "WARNING: socat did not stop cleanly, forcing kill..."
        pkill -f "socat.*ttyV${CHANNEL}"
        break
    fi
done

# Re-enable + start
svc -u "$SERVICE"
sleep 1

# Confirm
if pgrep -f "socat.*ttyV${CHANNEL}" >/dev/null; then
    echo "socat restarted successfully."
else
    echo "ERROR: socat failed to start."
fi
