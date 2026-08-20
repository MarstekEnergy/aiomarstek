"""Command builder for Marstek devices."""

from __future__ import annotations

import json
from typing import Any

from .const import (
    CMD_BATTERY_STATUS,
    CMD_DISCOVER,
    CMD_ES_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
)

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
