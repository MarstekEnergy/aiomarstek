"""Tests for Marstek device models."""

from aiomarstek import MarstekDeviceInfo


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
