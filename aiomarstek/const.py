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
