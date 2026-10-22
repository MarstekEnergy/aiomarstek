"""Tests for the extended component status parsing."""

from __future__ import annotations

import pytest

from aiomarstek import MarstekDeviceStatus


def test_es_status_parses_full_response() -> None:
    """ES.GetStatus fields documented in the Open API are all captured."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_es_status_response(
        {
            "bat_soc": 98,
            "bat_cap": 5120,
            "pv_power": 120,
            "ongrid_power": 100,
            "offgrid_power": 0,
            "bat_power": -50,
            "total_pv_energy": 0,
            "total_grid_output_energy": 2548,
            "total_grid_input_energy": 3273,
            "total_load_energy": 0,
        }
    )

    assert status.battery_soc == 98
    assert status.battery_capacity == 5120
    assert status.pv_power == 120
    assert status.ongrid_power == 100
    assert status.offgrid_power == 0
    assert status.battery_power == 50
    assert status.battery_status == "selling"
    assert status.total_grid_output_energy == 2548
    assert status.total_grid_input_energy == 3273


def test_es_status_prefers_dedicated_battery_power() -> None:
    """bat_power wins over the derived abs(ongrid_power) value."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_es_status_response(
        {"ongrid_power": 100, "bat_power": -300}
    )
    assert status.battery_power == 300


def test_es_status_derives_battery_power_without_bat_power() -> None:
    """Older devices only report ongrid_power; battery power is derived."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_es_status_response(
        {"ongrid_power": -100}
    )
    assert status.battery_power == 100


def test_es_mode_parses_ct_fields() -> None:
    """ES.GetMode CT fields are normalized and energies scaled to Wh."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_es_mode_response(
        {
            "mode": "Auto",
            "ongrid_power": 100,
            "offgrid_power": 0,
            "bat_soc": 98,
            "ct_state": 0,
            "a_power": 0,
            "b_power": 0,
            "c_power": 0,
            "total_power": 0,
            "input_energy": 3086320,
            "output_energy": 4487510,
        }
    )

    assert status.device_mode == "auto"
    assert status.ct_state is False
    assert status.total_power == 0
    assert status.ct_input_energy == 308632.0
    assert status.ct_output_energy == 448751.0


def test_battery_status_parses_response() -> None:
    """Bat.GetStatus fields are captured, including permission flags."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_battery_status_response(
        {
            "soc": 90,
            "charg_flag": True,
            "dischrg_flag": False,
            "bat_temp": 25.0,
            "bat_capacity": 256.0,
            "rated_capacity": 2560.0,
        }
    )

    assert status.battery_soc == 90
    assert status.charge_permission is True
    assert status.discharge_permission is False
    assert status.battery_temperature == 25.0
    assert status.battery_remaining_capacity == 256.0
    assert status.battery_rated_capacity == 2560.0


def test_battery_status_keeps_previous_on_partial_response() -> None:
    """Fields absent from a response keep their previous value."""
    status = MarstekDeviceStatus(
        device_ip="192.168.1.10", battery_temperature=25.0
    ).with_battery_status_response({"soc": 91})

    assert status.battery_soc == 91
    assert status.battery_temperature == 25.0


def test_wifi_status_parses_response() -> None:
    """Wifi.GetStatus network details are captured."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_wifi_status_response(
        {
            "wifi_mac": "620b0c877705",
            "ssid": "Hame",
            "rssi": -59,
            "sta_ip": "192.168.137.41",
            "sta_gate": "192.168.137.1",
            "sta_mask": "255.255.255.0",
            "sta_dns": "192.168.137.1",
        }
    )

    assert status.wifi_ssid == "Hame"
    assert status.wifi_rssi == -59
    assert status.wifi_ip == "192.168.137.41"
    assert status.wifi_gateway == "192.168.137.1"
    assert status.wifi_netmask == "255.255.255.0"
    assert status.wifi_dns == "192.168.137.1"


def test_wifi_status_accepts_null_fields() -> None:
    """Nulls reported for a disconnected WiFi are preserved."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_wifi_status_response(
        {"ssid": None, "rssi": -59, "sta_ip": None}
    )

    assert status.wifi_ssid is None
    assert status.wifi_ip is None
    assert status.wifi_rssi == -59


def test_ble_status_parses_response() -> None:
    """BLE.GetStatus connection state is captured."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_ble_status_response(
        {"state": "connect", "ble_mac": "50cf14640fac"}
    )

    assert status.ble_state == "connect"


def test_em_status_parses_response() -> None:
    """EM.GetStatus mirrors the ES.GetMode CT fields."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_em_status_response(
        {
            "ct_state": 1,
            "a_power": 10,
            "b_power": 20,
            "c_power": 30,
            "total_power": 60,
            "input_energy": 100,
            "output_energy": 200,
        }
    )

    assert status.ct_state is True
    assert status.a_power == 10
    assert status.b_power == 20
    assert status.c_power == 30
    assert status.total_power == 60
    assert status.ct_input_energy == 10.0
    assert status.ct_output_energy == 20.0


def test_battery_status_rejects_invalid_flags() -> None:
    """Non-boolean permission flags raise TypeError."""
    with pytest.raises(TypeError):
        MarstekDeviceStatus(device_ip="192.168.1.10").with_battery_status_response(
            {"charg_flag": "yes"}
        )


def test_ct_state_rejects_invalid_values() -> None:
    """CT state must be 0, 1 or null."""
    with pytest.raises(TypeError):
        MarstekDeviceStatus(device_ip="192.168.1.10").with_em_status_response(
            {"ct_state": 5}
        )


def test_hold_glitches_protects_ct_energy() -> None:
    """CT cumulative energy drops are held at the previous reading."""
    from aiomarstek import MONOTONIC_KEYS, hold_glitches

    # ct_input_energy and ct_output_energy are in the monotonic set.
    assert "ct_input_energy" in MONOTONIC_KEYS
    assert "ct_output_energy" in MONOTONIC_KEYS
    assert "total_load_energy" in MONOTONIC_KEYS

    previous = MarstekDeviceStatus(
        device_ip="192.168.1.10", ct_input_energy=308632.0, ct_output_energy=448751.0
    )
    current = MarstekDeviceStatus(
        device_ip="192.168.1.10", ct_input_energy=100.0, ct_output_energy=448752.0
    )
    held = hold_glitches(previous, current)
    # The drop is held; the rise is accepted.
    assert held.ct_input_energy == 308632.0
    assert held.ct_output_energy == 448752.0
