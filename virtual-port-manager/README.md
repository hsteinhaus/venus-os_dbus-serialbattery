\######################

Overview

\######################

The Virtual Port Manager provides a scalable, stable, and reproducible way to expose multiple JK‑PB BMS units over a single RS485‑TCP interface (e.g., Waveshare RS485‑to‑Ethernet (https://s.click.aliexpress.com/e/\_c4nhBqYP)) to Venus OS.

It creates virtual PTY devices (/dev/ttyV0, /dev/ttyV1, …) and manages:

* socat TCP→PTY bridges
* runit service supervision
* automatic restart logic
* clean shutdown
* multi‑BMS polling
* integration with dbus-serialbattery
* operator‑friendly restart/status tooling

This enables Venus OS to treat each BMS as if it were connected to a dedicated USB‑RS485 adapter — even when all BMS are connected to a single RS485 bus.





\######################

Features

\######################

* Supports up to 15 JK‑PB BMS on a single RS485 bus
* Creates stable virtual serial ports (/dev/ttyV0\_\_0x01, /dev/ttyV0\_\_0x02, …)
* Fully supervised via runit
* Automatic recovery if Waveshare reboots or drops TCP
* Clean restart scripts for:
* dbus‑serialbattery
* virtual port manager
* socat
* all channels
* Zero runtime artefacts in the repo (installer generates everything)
* Compatible with Holger’s multi‑BMS branch of dbus‑serialbattery
* Designed for upstream integration into the Victron ecosystem





\######################

Folder Structure

\######################

virtual-port-manager/

│

├── install.sh

├── uninstall.sh

├── restart.sh

├── restart\_socat.sh

├── status.sh

├── config.ini

└── VERSION





\######################

What’s NOT included

\######################

Runtime folders such as:

* /service/vsp-\*
* /service/socat-\*
* supervise/
* log/

These are created dynamically by the installer on the user’s Venus OS device.





\######################

Installation

\######################

SSH into your Venus OS device and run:

* sh install.sh



The installer will:

* create /data/etc/runit/ service templates
* create /service/ symlinks
* create virtual PTYs
* start socat bridges
* start dbus‑serialbattery instances
* validate connectivity to the Waveshare
* print status summary





\######################

Uninstallation

\######################

To remove all virtual ports and services:

* sh uninstall.sh

This will:

* stop all vsp‑\* and socat‑\* services
* remove runit service folders
* remove /service/ symlinks
* clean up PTY devices
* leave no residue on the system





\######################

Configuration

\######################

Edit config.ini to define:

* Waveshare IP/port
* virtual port name

Example:

\# IP address of the Waveshare RS485-to-Ethernet adapter

IP=10.0.0.3

\# TCP port of the Waveshare (usually 9999)

PORT=9999

\# Virtual serial port name

VIRTUAL\_PORT=/dev/ttyV0





\######################

Restarting Services

\######################

Restart dbus‑serialbattery

\#   restart-virtual-ports.sh        → restart channel 0

\#   restart-virtual-ports.sh 0      → restart channel 0

\#   restart-virtual-ports.sh all    → restart all channels

\#   restart-virtual-ports.sh 0 1 2  → restart channels 0,1,2

\#   restart\_socat.sh                → restart socat only





\######################

Status

\######################

Check the health of all virtual ports:

* sh status.sh

Shows:

* VPM Version
* Waveshare TCP config
* dbus‑serialbattery PID
* socat PID
* PTY status
* dbus-serialbattery service status
* watchdog status
* TCP connectivity
* last 10 watchdog entries





\######################

Compatibility

\######################

Tested with:

* Venus OS 3.72
* Raspberry Pi 3B+
* Waveshare 4/8-Channel RS485‑to‑Ethernet POE in TCP server mode (https://s.click.aliexpress.com/e/\_c4nhBqYP)
* JK‑PB BMS V14, V15 and V19, firmware V14.24, V15.31, V19.31, V19.31B
* @hsteinhaus multi‑BMS branch of dbus‑serialbattery (https://github.com/hsteinhaus/venus-os\_dbus-serialbattery)





\######################

Notes for Developers

\######################

This project is designed to be integrated into:

* @hsteinhaus dbus-serialbattery (https://github.com/hsteinhaus/venus-os\_dbus-serialbattery)
* @mr-manuel upstream Victron driver (https://github.com/mr-manuel/venus-os\_dbus-serialbattery)
* future multi‑port RS485 architectures

The installer is idempotent and safe to run multiple times.





\######################

Acknowledgements

\######################

@hsteinhaus — multi‑BMS driver development (https://github.com/hsteinhaus/venus-os\_dbus-serialbattery)

@mr-manuel — upstream Victron integration (https://github.com/mr-manuel/venus-os\_dbus-serialbattery)

