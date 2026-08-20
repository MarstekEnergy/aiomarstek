"""Tests for the public constants."""

from __future__ import annotations

from aiomarstek import const


def test_default_udp_port() -> None:
    """Marstek devices listen on UDP port 30000."""
    assert const.DEFAULT_UDP_PORT == 30000


def test_discovery_timeout() -> None:
    """Discovery waits ten seconds for responses."""
    assert const.DISCOVERY_TIMEOUT == 10.0


def test_command_methods() -> None:
    """Command method strings match the device protocol."""
    assert const.CMD_DISCOVER == "Marstek.GetDevice"
    assert const.CMD_BATTERY_STATUS == "Bat.GetStatus"
    assert const.CMD_ES_STATUS == "ES.GetStatus"
    assert const.CMD_ES_MODE == "ES.GetMode"
    assert const.CMD_PV_GET_STATUS == "PV.GetStatus"
