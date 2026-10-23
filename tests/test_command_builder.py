"""Tests for the command builder."""

from __future__ import annotations

import json

from aiomarstek import (
    MODE_MANUAL,
    MarstekManualSlot,
    MarstekModeConfig,
    command_builder,
)
from aiomarstek.const import (
    CMD_BATTERY_STATUS,
    CMD_DISCOVER,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
)


def _parse(command: str) -> dict[str, object]:
    """Parse a command JSON string into a dict."""
    return json.loads(command)


def test_build_command_increments_request_id() -> None:
    """Request ID increments on every built command."""
    command_builder.reset_request_id()
    first = _parse(command_builder.build_command("Test.Method"))
    second = _parse(command_builder.build_command("Test.Method"))
    assert first["id"] == 1
    assert second["id"] == 2


def test_build_command_structure() -> None:
    """A command carries id, method and params."""
    command_builder.reset_request_id()
    command = _parse(command_builder.build_command("Test.Method", {"key": "value"}))
    assert command == {"id": 1, "method": "Test.Method", "params": {"key": "value"}}


def test_build_command_defaults_to_empty_params() -> None:
    """Omitted params default to an empty object."""
    command_builder.reset_request_id()
    command = _parse(command_builder.build_command("Test.Method"))
    assert command["params"] == {}


def test_discover_command() -> None:
    """Discovery command uses the discovery method and BLE MAC zero."""
    command_builder.reset_request_id()
    command = _parse(command_builder.discover())
    assert command["method"] == CMD_DISCOVER
    assert command["params"] == {"ble_mac": "0"}


def test_get_battery_status() -> None:
    """Battery status command targets the requested device."""
    command_builder.reset_request_id()
    command = _parse(command_builder.get_battery_status(7))
    assert command["method"] == CMD_BATTERY_STATUS
    assert command["params"] == {"id": 7}


def test_get_es_status() -> None:
    """ES status command targets the requested device."""
    command_builder.reset_request_id()
    command = _parse(command_builder.get_es_status(3))
    assert command["method"] == CMD_ES_STATUS
    assert command["params"] == {"id": 3}


def test_get_es_mode() -> None:
    """ES mode command targets the requested device."""
    command_builder.reset_request_id()
    command = _parse(command_builder.get_es_mode(5))
    assert command["method"] == CMD_ES_MODE
    assert command["params"] == {"id": 5}


def test_get_pv_status() -> None:
    """PV status command targets the requested device."""
    command_builder.reset_request_id()
    command = _parse(command_builder.get_pv_status(2))
    assert command["method"] == CMD_PV_GET_STATUS
    assert command["params"] == {"id": 2}


def test_reset_request_id() -> None:
    """Resetting the counter starts IDs back at one."""
    command_builder.get_next_request_id()
    command_builder.get_next_request_id()
    command_builder.reset_request_id()
    assert command_builder.get_next_request_id() == 1


def test_set_es_mode_non_manual_payload() -> None:
    """A non-Manual mode builds the documented single config object."""
    command_builder.reset_request_id()
    command = _parse(command_builder.set_es_mode(MarstekModeConfig(mode="ai")))
    assert command["method"] == CMD_ES_SET_MODE
    config = command["params"]["config"]
    # AI mode carries an ai_cfg enable block, never a manual_cfg.
    assert "ai_cfg" in config
    assert "manual_cfg" not in config


def test_set_es_mode_manual_emits_array_cfg() -> None:
    """set_es_mode for Manual emits manual_cfg as an array (bulk form).

    This is the legacy/bulk shape; the documented per-slot form is a single
    object (see test_set_es_mode_slot_emits_single_object). Documented so a
    future change to the shape is caught.
    """
    command_builder.reset_request_id()
    slot = MarstekManualSlot(
        time_num=0,
        start_time="00:00",
        end_time="06:00",
        week_set=127,
        power=800,
        manual_set=1,
    )
    command = _parse(
        command_builder.set_es_mode(
            MarstekModeConfig(mode=MODE_MANUAL, manual_slots=[slot])
        )
    )
    manual_cfg = command["params"]["config"]["manual_cfg"]
    assert isinstance(manual_cfg, list)
    assert len(manual_cfg) == 1


def test_set_es_mode_slot_emits_single_object() -> None:
    """set_es_mode_slot emits manual_cfg as a single object (documented form)."""
    command_builder.reset_request_id()
    slot = MarstekManualSlot(
        time_num=3,
        start_time="10:00",
        end_time="12:00",
        week_set=1,
        power=500,
        manual_set=2,
    )
    command = _parse(command_builder.set_es_mode_slot(slot))
    assert command["method"] == CMD_ES_SET_MODE
    config = command["params"]["config"]
    assert config["mode"] == "Manual"
    # The documented form is a single slot object, not an array.
    assert isinstance(config["manual_cfg"], dict)
    assert config["manual_cfg"]["time_num"] == 3
