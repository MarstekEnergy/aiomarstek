"""Tests for Marstek device models."""

import pytest

from aiomarstek import MarstekDeviceInfo, MarstekDeviceStatus


def test_device_info_parses_discovery_response() -> None:
    """Test parsing a discovery response into a device model."""
    device = MarstekDeviceInfo.from_response(
        {
            "id": 1,
            "device": "VenusE 3.0",
            "ver": 2,
            "wifi_name": "home",
            "ip": "192.168.1.10",
            "wifi_mac": "AA:BB:CC:DD:EE:FF",
            "ble_mac": "11:22:33:44:55:66",
        }
    )

    assert device == MarstekDeviceInfo(
        id=1,
        device_type="VenusE 3.0",
        version=2,
        wifi_name="home",
        ip="192.168.1.10",
        wifi_mac="AA:BB:CC:DD:EE:FF",
        ble_mac="11:22:33:44:55:66",
        mac="AA:BB:CC:DD:EE:FF",
    )
    assert device.stable_id == "AA:BB:CC:DD:EE:FF"


def test_device_info_uses_host_when_response_has_no_ip() -> None:
    """Test parsing uses the requested host when no IP is returned."""
    device = MarstekDeviceInfo.from_response({"device": "VenusE 3.0"}, "192.168.1.10")

    assert device.ip == "192.168.1.10"


def test_device_status_parses_protocol_values() -> None:
    """Test status parsing follows the Open API field types and values."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10")

    status = status.with_es_mode_response(
        {"bat_soc": 98, "ongrid_power": -100.5, "mode": "Auto"}
    )
    status = status.with_pv_status_response(
        {
            "pv1_power": 42.5,
            "pv1_voltage": 10.5,
            "pv1_current": 4.25,
            "pv1_state": 1,
        }
    )

    assert status.battery_soc == 98
    assert status.battery_power == 100.5
    assert status.device_mode == "auto"
    assert status.battery_status == "charging"
    assert status.pv1_power == 42.5
    assert status.pv1_voltage == 10.5
    assert status.pv1_current == 4.25
    assert status.pv1_state == "working"
    assert status.has_value("pv2_power") is False


@pytest.mark.parametrize(
    ("response", "match"),
    [
        pytest.param({"bat_soc": "98"}, "bat_soc", id="numeric_string"),
        pytest.param({"ongrid_power": True}, "ongrid_power", id="boolean_power"),
        pytest.param({"mode": "unsupported"}, "mode", id="unknown_mode"),
        pytest.param({"pv1_state": 2}, "pv1_state", id="unknown_pv_state"),
    ],
)
def test_device_status_rejects_invalid_protocol_values(
    response: dict[str, object], match: str
) -> None:
    """Test invalid protocol values are rejected by the library."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10")

    with pytest.raises(TypeError, match=match):
        if "pv1_state" in response:
            status.with_pv_status_response(response)
        else:
            status.with_es_mode_response(response)
