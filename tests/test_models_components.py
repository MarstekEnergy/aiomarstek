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


def test_battery_status_parses_health_and_lifetime_energy() -> None:
    """The Bat.GetStatus health/lifetime fields are captured and scaled to Wh."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_battery_status_response(
        {
            "soc": 29,
            # 0.1 kWh units -> Wh.
            "bat_soh": 99.0,
            "bat_total_input_energy": 1220,
            "bat_total_output_energy": 975,
            # 0.001 kWh units -> Wh (already Wh).
            "backup_input_energy": 0,
            "backup_output_energy": 12,
        }
    )

    assert status.battery_soh == 99.0
    assert status.battery_total_input_energy == 122000
    assert status.battery_total_output_energy == 97500
    assert status.backup_input_energy == 0
    assert status.backup_output_energy == 12


def test_sys_info_parses_dod() -> None:
    """Sys.GetInfo reports the discharge depth the device actually uses."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_sys_info_response(
        {"id": 0, "dod": 40, "ble_state": 0, "ble_adv_en": 1, "led_state": 0}
    )

    assert status.dod == 40


def test_sys_info_reports_the_settings_it_mirrors() -> None:
    """ble_adv_en/led_state mirror the write-only Bluetooth and LED controls."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_sys_info_response(
        {"id": 0, "ble_adv_en": 1, "led_state": 0}
    )

    assert status.bluetooth_advertising is True
    assert status.led is False

    status = status.with_sys_info_response({"id": 0, "ble_adv_en": 0, "led_state": 1})

    assert status.bluetooth_advertising is False
    assert status.led is True


def test_sys_info_maps_power_version_code_to_watts() -> None:
    """set_ver is a code; Set.Ver takes watts, so it is mapped."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_sys_info_response(
        {"id": 0, "set_ver": 0}
    )
    assert status.power_version == 2500

    for code, watts in ((1, 800), (3, 2200), (5, 1500)):
        status = MarstekDeviceStatus(
            device_ip="192.168.1.10", power_version_code=code
        ).with_sys_info_response({"id": 0})
        assert status.power_version == watts

    # An undocumented code maps to nothing instead of a guessed watt figure.
    unknown = MarstekDeviceStatus(device_ip="192.168.1.10").with_sys_info_response(
        {"id": 0, "set_ver": 2}
    )
    assert unknown.power_version is None


def test_sys_info_parses_firmware_versions() -> None:
    """The four firmware versions in Sys.GetInfo are kept as reported."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_sys_info_response(
        {
            "id": 0,
            "ems_app_ver": 1513,
            "boot_ver": 18,
            "bms_ver": 115,
            "inv_ver": 1207,
        }
    )

    assert status.ems_version == 1513
    assert status.boot_version == 18
    assert status.bms_version == 115
    assert status.inverter_version == 1207


def test_error_response_parses_hex_strings() -> None:
    """Err.Get reports hexadecimal strings, parsed base 16 into bitmasks."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_error_response(
        {"EMS_ERR": "0x800", "BMS_ERR": "0x0", "INV_ERR": "0", "MPPT_ERR": "0x10"}
    )

    assert status.ems_error == 0x800
    assert status.bms_error == 0
    assert status.inv_error == 0
    assert status.mppt_error == 0x10


def test_error_response_accepts_documented_numbers() -> None:
    """A firmware that sends real numbers instead of strings still parses."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_error_response(
        {"EMS_ERR": 2048, "BMS_ERR": None}
    )

    assert status.ems_error == 2048
    assert status.bms_error is None


def test_error_response_rejects_non_hex_values() -> None:
    """A malformed error code raises instead of silently reading as zero."""
    with pytest.raises(ValueError):
        MarstekDeviceStatus(device_ip="192.168.1.10").with_error_response(
            {"EMS_ERR": "nope"}
        )


def test_energy_response_scales_to_wh() -> None:
    """Energy.GetData counters are 0.01 kWh and normalized to Wh."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_energy_response(
        {
            "day_charge": 12,
            "day_discharge": 45,
            "mon_charge": 380,
            "mon_discharge": 1120,
            "total_charge": 5600,
            "total_discharge": 18400,
        }
    )

    assert status.energy_day_charge == 120
    assert status.energy_day_discharge == 450
    assert status.energy_month_charge == 3800
    assert status.energy_month_discharge == 11200
    assert status.energy_total_charge == 56000
    assert status.energy_total_discharge == 184000


def test_documented_scales_reproduce_measured_counters() -> None:
    """The Rev 4.0 reply scales match the measured same-instant readings.

    The Rev 4.0 doc error report (HE-2) measured one instant on a VenusE 3.0:
    Bat.GetStatus.bat_total_input_energy 213 (21.3 kWh), Energy.GetData
    .total_charge 297 (2.97 kWh), ES.GetStatus.total_grid_input_energy 2973
    (2.97 kWh) and EM.GetStatus.input_energy 2306800 (230.68 kWh).
    """
    status = MarstekDeviceStatus(device_ip="192.168.1.10")

    bat = status.with_battery_status_response({"bat_total_input_energy": 213})
    energy = status.with_energy_response({"total_charge": 297})
    grid = status.with_es_status_response({"total_grid_input_energy": 2973})
    em = status.with_em_status_response({"input_energy": 2306800})

    assert bat.battery_total_input_energy == 21300  # 0.1 kWh -> 21.3 kWh
    assert energy.energy_total_charge == 2970  # 0.01 kWh -> 2.97 kWh
    assert grid.total_grid_input_energy == 2973  # plain Wh -> 2.97 kWh
    assert em.ct_input_energy == 230680.0  # 0.1 Wh -> 230.68 kWh


def test_monotonic_counters_have_a_documented_unit() -> None:
    """Every monotonic counter must declare its reply scale.

    The scales are uneven, so a counter missing from FIELD_UNITS would silently
    be passed through unscaled.
    """
    from aiomarstek.models import FIELD_UNITS, MONOTONIC_KEYS

    assert set(MONOTONIC_KEYS) <= set(FIELD_UNITS)


def test_lifetime_counters_are_glitch_protected() -> None:
    """Battery and Energy.GetData lifetime counters never regress."""
    from aiomarstek import MONOTONIC_KEYS, hold_glitches

    for key in (
        "battery_total_input_energy",
        "battery_total_output_energy",
        "backup_input_energy",
        "backup_output_energy",
        "energy_total_charge",
        "energy_total_discharge",
    ):
        assert key in MONOTONIC_KEYS

    previous = MarstekDeviceStatus(
        device_ip="192.168.1.10", energy_total_charge=56000, energy_day_charge=120
    )
    current = MarstekDeviceStatus(
        device_ip="192.168.1.10", energy_total_charge=10, energy_day_charge=0
    )
    held = hold_glitches(previous, current)

    # The lifetime counter is held; the day counter is allowed to reset.
    assert held.energy_total_charge == 56000
    assert held.energy_day_charge == 0


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
