"""Command builder for Marstek devices."""

from __future__ import annotations

import json
from typing import Any

from .const import (
    CMD_BATTERY_STATUS,
    CMD_BLE_ADV,
    CMD_BLE_STATUS,
    CMD_DISCOVER,
    CMD_DOD_SET,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_FACTORY_RESET,
    CMD_LED_CTRL,
    CMD_PV_GET_STATUS,
    CMD_SET_VER,
    CMD_WIFI_STATUS,
)
from .models import MarstekModeConfig

_request_id = 0


def get_next_request_id() -> int:
    """Get next request ID."""
    global _request_id
    _request_id += 1
    return _request_id


def reset_request_id() -> None:
    """Reset request ID counter."""
    global _request_id
    _request_id = 0


def build_command(method: str, params: dict[str, Any] | None = None) -> str:
    """Build command JSON string."""
    command = {
        "id": get_next_request_id(),
        "method": method,
        "params": params or {},
    }
    return json.dumps(command)


def discover() -> str:
    """Device discovery command."""
    return build_command(CMD_DISCOVER, {"ble_mac": "0"})


def get_battery_status(device_id: int = 0) -> str:
    """Battery status query command."""
    return build_command(CMD_BATTERY_STATUS, {"id": device_id})


def get_es_status(device_id: int = 0) -> str:
    """Get device power status and statistics command."""
    return build_command(CMD_ES_STATUS, {"id": device_id})


def get_es_mode(device_id: int = 0) -> str:
    """Get device operating mode and battery info command."""
    return build_command(CMD_ES_MODE, {"id": device_id})


def get_pv_status(device_id: int = 0) -> str:
    """Get device PV status command."""
    return build_command(CMD_PV_GET_STATUS, {"id": device_id})


def get_wifi_status(device_id: int = 0) -> str:
    """Get device network information command."""
    return build_command(CMD_WIFI_STATUS, {"id": device_id})


def get_ble_status(device_id: int = 0) -> str:
    """Get device Bluetooth connection status command."""
    return build_command(CMD_BLE_STATUS, {"id": device_id})


def get_em_status(device_id: int = 0) -> str:
    """Get energy meter / CT status command."""
    return build_command(CMD_EM_STATUS, {"id": device_id})


def set_es_mode(config: MarstekModeConfig, device_id: int = 0) -> str:
    """Set device operating mode command."""
    return build_command(
        CMD_ES_SET_MODE, {"id": device_id, "config": config.to_payload()}
    )


def set_dod(value: int, device_id: int = 0) -> str:
    """Set discharge depth command (valid range 30-88)."""
    return build_command(CMD_DOD_SET, {"id": device_id, "value": value})


def set_ble_adv(enabled: bool, device_id: int = 0) -> str:
    """Set Bluetooth advertising command.

    The Open API's ``enable`` parameter is inverted for this command:
    0 turns advertising on and 1 turns it off, so ``enabled=True`` sends 0.
    """
    return build_command(CMD_BLE_ADV, {"id": device_id, "enable": 0 if enabled else 1})


def set_led(on: bool, device_id: int = 0) -> str:
    """Set front-panel LED command (state 1: on, 0: off)."""
    return build_command(CMD_LED_CTRL, {"id": device_id, "state": 1 if on else 0})


def set_power_version(version: int, device_id: int = 0) -> str:
    """Set charge and discharge power version command, in watts."""
    return build_command(CMD_SET_VER, {"id": device_id, "version": version})


def factory_reset(keep_accumulated_data: bool = False, device_id: int = 0) -> str:
    """Factory reset command.

    Type 1 clears accumulated data, type 2 keeps it.
    """
    return build_command(
        CMD_FACTORY_RESET,
        {"id": device_id, "type": 2 if keep_accumulated_data else 1},
    )
