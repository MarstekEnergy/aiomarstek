"""Tests for the UDP client."""

from __future__ import annotations

from typing import Any

import pytest

from aiomarstek import MarstekDeviceInfo, MarstekDeviceStatus, MarstekUDPClient


def _device_result(**overrides: Any) -> dict[str, Any]:
    """Build a discovery result dict with sensible defaults."""
    result = {
        "id": 1,
        "device": "VENUS E",
        "ver": 3,
        "wifi_name": "home",
        "ip": "192.168.1.10",
        "wifi_mac": "AA:BB:CC:DD:EE:FF",
        "ble_mac": "",
    }
    result.update(overrides)
    return result


async def test_discover_devices_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate device entries are collapsed to a single device."""
    client = MarstekUDPClient()
    responses = [
        {"result": _device_result()},
        {"result": _device_result()},
    ]

    async def fake_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return responses

    monkeypatch.setattr(client, "send_broadcast_request", fake_broadcast)

    devices = await client.discover_devices(use_cache=False)
    assert len(devices) == 1
    device = devices[0]
    assert device == MarstekDeviceInfo(
        id=1,
        device_type="VENUS E",
        version=3,
        wifi_name="home",
        ip="192.168.1.10",
        wifi_mac="AA:BB:CC:DD:EE:FF",
        ble_mac="",
        mac="AA:BB:CC:DD:EE:FF",
    )


async def test_discover_devices_skips_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Responses without a result object are ignored."""
    client = MarstekUDPClient()

    async def fake_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"raw": "no result"}, {"result": _device_result(ip="192.168.1.11")}]

    monkeypatch.setattr(client, "send_broadcast_request", fake_broadcast)

    devices = await client.discover_devices(use_cache=False)
    assert len(devices) == 1
    assert devices[0].ip == "192.168.1.11"


async def test_discover_devices_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second discovery call reuses the cached result."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"result": _device_result()}]

    monkeypatch.setattr(client, "send_broadcast_request", fake_broadcast)

    first = await client.discover_devices()
    second = await client.discover_devices()
    assert calls == 1
    assert first == second


async def test_polling_pause_resume() -> None:
    """Polling can be paused and resumed per device."""
    client = MarstekUDPClient()
    assert client.is_polling_paused("1.2.3.4") is False
    await client.pause_polling("1.2.3.4")
    assert client.is_polling_paused("1.2.3.4") is True
    await client.resume_polling("1.2.3.4")
    assert client.is_polling_paused("1.2.3.4") is False


async def test_get_device_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Device info returns a normalized device model."""
    client = MarstekUDPClient()
    expected = {"ip": "192.168.1.5", "device": "VENUS E"}

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"result": expected}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert await client.get_device_info("192.168.1.5") == MarstekDeviceInfo(
        id=None,
        device_type="VENUS E",
        version=0,
        wifi_name="",
        ip="192.168.1.5",
        wifi_mac="",
        ble_mac="",
        mac="",
    )


async def test_get_device_info_requires_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response without a result object raises TypeError."""
    client = MarstekUDPClient()

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    with pytest.raises(TypeError):
        await client.get_device_info("192.168.1.5")


async def test_get_device_status_returns_normalized_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status responses are parsed by the library before being returned."""
    client = MarstekUDPClient()
    responses = iter(
        [
            {"result": {"bat_soc": 90, "ongrid_power": 100, "mode": 2}},
            {
                "result": {
                    "pv1_power": 42.5,
                    "pv1_voltage": 10.5,
                    "pv1_current": 4.25,
                    "pv1_state": 1,
                }
            },
        ]
    )

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return next(responses)

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert await client.get_device_status("192.168.1.10", delay_ms=0) == (
        MarstekDeviceStatus(
            device_ip="192.168.1.10",
            battery_soc=90,
            battery_power=100,
            device_mode="manual",
            battery_status="selling",
            pv1_power=42.5,
            pv1_voltage=10.5,
            pv1_current=4.25,
            pv1_state="working",
        )
    )


async def test_send_request_rejects_invalid_json() -> None:
    """Non-JSON messages raise ValueError before any socket work."""
    client = MarstekUDPClient()
    client._socket = True  # Skip real socket setup.
    with pytest.raises(ValueError):
        await client.send_request("not-json", "192.168.1.5")


async def test_send_request_requires_request_id() -> None:
    """Messages without an id raise ValueError before any socket work."""
    client = MarstekUDPClient()
    client._socket = True  # Skip real socket setup.
    with pytest.raises(ValueError):
        await client.send_request('{"method": "Test"}', "192.168.1.5")


@pytest.mark.parametrize("addresses", [[], None])
async def test_discover_devices_no_broadcast_addresses(
    monkeypatch: pytest.MonkeyPatch, addresses: list[str] | None
) -> None:
    """An empty address list still yields an empty, cached result."""
    client = MarstekUDPClient()

    async def fake_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(client, "send_broadcast_request", fake_broadcast)

    assert await client.discover_devices(broadcast_addresses=addresses) == []
