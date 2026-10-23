"""Constants for the Marstek client library."""

from __future__ import annotations

from typing import Final

DEFAULT_UDP_PORT: Final = 30000
DISCOVERY_TIMEOUT: Final = 10.0

CMD_DISCOVER: Final = "Marstek.GetDevice"
CMD_BATTERY_STATUS: Final = "Bat.GetStatus"
CMD_ES_STATUS: Final = "ES.GetStatus"
CMD_ES_MODE: Final = "ES.GetMode"
CMD_ES_SET_MODE: Final = "ES.SetMode"
CMD_PV_GET_STATUS: Final = "PV.GetStatus"
CMD_WIFI_STATUS: Final = "Wifi.GetStatus"
CMD_BLE_STATUS: Final = "BLE.GetStatus"
CMD_EM_STATUS: Final = "EM.GetStatus"
CMD_DOD_SET: Final = "DOD.SET"
CMD_BLE_ADV: Final = "Ble.Adv"
CMD_LED_CTRL: Final = "Led.Ctrl"
CMD_SET_VER: Final = "Set.Ver"
CMD_FACTORY_RESET: Final = "Reset.Factory"

# Set result values a device may report for a successful write. The Open API
# document spells the boolean as "ture" in every example response, so string
# spellings are accepted alongside real booleans.
TRUTHY_SET_RESULTS: Final = frozenset({True, "true", "True", "ture", "Ture"})

# Discharge depth bounds documented for the DOD.SET command.
DOD_MIN: Final = 30
DOD_MAX: Final = 88

# Power versions documented for the Set.Ver command, in watts.
POWER_VERSIONS: Final = (800, 1200, 1500, 2200, 2500)

# Polls between refreshes of the slow-changing status commands (WiFi, BLE
# and EM). They answer within the same poll cycle but rarely change, so
# querying them on every poll only adds UDP round trips.
EXTRAS_INTERVAL: Final = 5

# Minimum quiet time, in seconds, between a device's last response and the
# next request sent to it. Field logs show Marstek devices silently drop a
# request that arrives within moments of their previous response, while
# requests sent after this much quiet time are answered reliably. The Home
# Assistant integration enables this gap via the client's ``min_request_gap``
# option; the library default keeps it disabled.
REQUEST_QUIET_GAP: Final = 2.5

# Minimum pause before sending a write command, in milliseconds. Some
# Marstek devices drop a UDP request that arrives within milliseconds of
# the previous response, so a write issued right after an in-flight poll
# finishes can be silently lost. This settle delay lets the device return
# to its receive loop first.
WRITE_SETTLE_MS: Final = 500

# How many times a write command is retried after the device stays silent.
# Even with the request quiet gap, a write can land unlucky and be dropped;
# a single retry spaced by the gap recovers the common case without making a
# one-off drop surface as a failure to automations and the UI.
WRITE_MAX_RETRIES: Final = 1

# How often the UDP listener watchdog checks that the background receive
# task is alive and draining the socket. A dead or stuck listener means
# replies are never read (the OS receive buffer fills and drops them) while
# sends still succeed, which looks exactly like a device that never answers.
LISTENER_WATCHDOG_INTERVAL: Final = 30.0

# If the listener task is alive but has not successfully read a packet for
# this long AND it had read one earlier this session, it is presumed stuck:
# a healthy listener simply blocks on recvfrom waiting for the next reply,
# so a transition from "was receiving" to "long silence" means it stopped
# draining. The watchdog then restarts it and rebounds the socket.
LISTENER_STUCK_SILENCE: Final = 120.0

# Consecutive OSError failures from sock_recvfrom indicate the socket
# itself is bad; after this many in a row the socket is closed and rebound.
RECV_ERROR_REBIND_THRESHOLD: Final = 5
