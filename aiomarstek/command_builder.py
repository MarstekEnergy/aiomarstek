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
from .models import MarstekManualSlot, MarstekModeConfig

_request_id = 0


def get_next_request_id() -> int:
    """Get next request ID."""
    global _request_id
    _request_id += 1
    return _request_id


def reset_request_id() -> None:
    """Reset request ID counter.

    .. warning::

        Intended for tests and fresh initialization only. It MUST NOT be
        called while any request is in flight. ``MarstekUDPClient`` keys its
        pending responses by request id, so resetting the counter to zero
        makes the next :func:`build_command` reuse ids that may still be
        awaited: a response to the new request would then resolve an older
        Future, or the new request's Future would overwrite the older entry in
        the pending table, delivering a reply to the wrong caller (or leaving
        the original waiter stuck until its timeout). Ensure no
        ``MarstekUDPClient`` is active -- equivalently, that its
        ``_pending_requests`` and ``_broadcast_responses`` are empty -- before
        calling.
    """
    global _request_id
    _request_id = 0


def build_command(method: str, params: dict[str, Any] | None = None) -> str:
    """Build command JSON string.

    The wire format has no authentication: a packet is only ``id``, ``method``
    and ``params``, with no token, nonce or signature, and the device is not
    known to verify who sent it. The trust boundary is therefore the network —
    any host that can reach the device port can issue every command here,
    including ``ES.SetMode`` and ``FactoryReset``. Keep devices on a segment
    that only trusted hosts can reach; see the README's security section.
    """
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
    """Build an ES.SetMode command for the given mode config.

    For non-Manual modes this is the command to send. For Manual mode this
    emits ``manual_cfg`` as an *array* of slots (a bulk form); the Open API
    (Rev3.1.2) documents ``manual_cfg`` as a single slot object identified by
    ``time_num``, so per-slot setting should use :func:`set_es_mode_slot`
    instead -- which :meth:`MarstekUDPClient.set_device_mode` does, sending
    one ES.SetMode per slot. Use this builder for Manual only if the firmware
    is known to accept the array form.
    """
    return build_command(
        CMD_ES_SET_MODE, {"id": device_id, "config": config.to_payload()}
    )


def set_es_mode_slot(slot: MarstekManualSlot, device_id: int = 0) -> str:
    """Build a single-slot Manual-mode ES.SetMode command.

    The Open API documents ``manual_cfg`` as a single slot object
    identified by ``time_num`` (0-9), so a Manual schedule is set one
    slot at a time rather than as one array-valued command.
    """
    return build_command(
        CMD_ES_SET_MODE,
        {
            "id": device_id,
            "config": {"mode": "Manual", "manual_cfg": slot.to_payload()},
        },
    )


def set_dod(value: int, device_id: int = 0) -> str:
    """Set discharge depth command (valid range 30-88)."""
    return build_command(CMD_DOD_SET, {"id": device_id, "value": value})


def set_ble_adv(enabled: bool, device_id: int = 0) -> str:
    """Set Bluetooth advertising command.

    Ble.Adv controls the device's Bluetooth lock (Ble_block); its
    ``enable`` parameter refers to the lock, not to advertising:
    0 enables the lock (advertising off) and 1 disables the lock
    (advertising on), so ``enabled=True`` sends 1.
    """
    return build_command(CMD_BLE_ADV, {"id": device_id, "enable": 1 if enabled else 0})


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
