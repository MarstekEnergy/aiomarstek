"""Tests for the UDP client."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import ANY

import pytest

from aiomarstek import (
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekNoResponse,
    MarstekUDPClient,
    discover,
    get_es_status,
)


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
        mac="",
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
    requests: list[dict[str, object]] = []
    responses = iter(
        [
            {
                "result": {
                    "bat_soc": 90,
                    "ongrid_power": 100,
                    "total_pv_energy": 281,
                }
            },
            {"result": {"mode": 2}},
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
        requests.append(json.loads(args[0]))
        return next(responses)

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert await client.get_device_status("192.168.1.10", delay_ms=0) == (
        MarstekDeviceStatus(
            device_ip="192.168.1.10",
            battery_soc=90,
            battery_power=100,
            device_mode="manual",
            battery_status="selling",
            total_pv_energy=2810,
            pv1_power=42.5,
            pv1_voltage=10.5,
            pv1_current=4.25,
            pv1_state="working",
        )
    )


async def test_get_device_status_tolerates_unsupported_pv_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devices without PV.GetStatus still return ES status data."""
    client = MarstekUDPClient()
    requests: list[dict[str, object]] = []
    responses = iter(
        [
            {"result": {"bat_soc": 90, "ongrid_power": -100}},
            {"result": {"mode": 0}},
            {"error": {"code": -32601, "message": "Method not found"}},
        ]
    )

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        requests.append(json.loads(args[0]))
        return next(responses)

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert await client.get_device_status("192.168.1.10", delay_ms=0) == (
        MarstekDeviceStatus(
            device_ip="192.168.1.10",
            battery_soc=90,
            battery_power=100,
            device_mode="auto",
            battery_status="charging",
        )
    )
    assert [(request["method"], request["params"]) for request in requests] == [
        ("ES.GetStatus", {"id": 0}),
        ("ES.GetMode", {"id": 1}),
        ("PV.GetStatus", {"id": 0}),
    ]


async def test_get_device_status_falls_back_to_es_mode_id_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devices using ES device ID zero retain their status data."""
    client = MarstekUDPClient()
    requests: list[dict[str, object]] = []
    responses = iter(
        [
            {"error": {"code": -32601, "message": "Method not found"}},
            TimeoutError(),
            {"result": {"bat_soc": 75, "ongrid_power": 50, "mode": 4}},
            {"error": {"code": -32601, "message": "Method not found"}},
        ]
    )

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        requests.append(json.loads(args[0]))
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert await client.get_device_status("192.168.1.11", delay_ms=0) == (
        MarstekDeviceStatus(
            device_ip="192.168.1.11",
            battery_soc=75,
            battery_power=50,
            device_mode="ups",
            battery_status="selling",
        )
    )
    assert [(request["method"], request["params"]) for request in requests] == [
        ("ES.GetStatus", {"id": 0}),
        ("ES.GetMode", {"id": 1}),
        ("ES.GetMode", {"id": 0}),
        ("PV.GetStatus", {"id": 0}),
    ]


async def test_get_device_status_skips_known_unsupported_pv_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device rejecting PV.GetStatus is not queried again."""
    client = MarstekUDPClient()
    requests: list[dict[str, object]] = []

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request = json.loads(args[0])
        requests.append(request)
        if request["method"] == "PV.GetStatus":
            return {"error": {"code": -32601, "message": "Method not found"}}
        if request["method"] == "ES.GetStatus":
            return {"result": {"bat_soc": 90}}
        return {"result": {"mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=0)
    await client.get_device_status("192.168.1.10", delay_ms=0)

    assert [request["method"] for request in requests] == [
        "ES.GetStatus",
        "ES.GetMode",
        "PV.GetStatus",
        "ES.GetStatus",
        "ES.GetMode",
    ]


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


@pytest.fixture
async def loopback_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[MarstekUDPClient, socket.socket, list[int]]]:
    """Client listening on an ephemeral loopback socket with sends captured."""
    client = MarstekUDPClient()
    client._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client._socket.bind(("127.0.0.1", 0))
    client._socket.setblocking(False)
    sent_ids: list[int] = []

    async def fake_send(message: str, *args: Any) -> None:
        sent_ids.append(json.loads(message)["id"])

    monkeypatch.setattr(client, "_send_udp_message", fake_send)
    device = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    device.connect(client._socket.getsockname())
    yield client, device, sent_ids
    device.close()
    await client.async_cleanup()


async def _wait_for(predicate: Callable[[], object]) -> None:
    """Wait until predicate is true, failing after one second."""
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_unicast_responses_are_not_retained(
    loopback_client: tuple[MarstekUDPClient, socket.socket, list[int]],
) -> None:
    """Answered and unsolicited responses are not kept after delivery."""
    client, device, sent_ids = loopback_client

    for _ in range(3):
        request = asyncio.create_task(
            client.send_request(get_es_status(0), "192.168.1.10", timeout=1)
        )
        await _wait_for(lambda: sent_ids)
        device.send(json.dumps({"id": sent_ids.pop(), "result": {}}).encode())
        assert await request == {"id": ANY, "result": {}}

    device.send(json.dumps({"id": 10**6, "result": {}}).encode())
    await asyncio.sleep(0.05)

    assert client._pending_requests == {}
    assert client._broadcast_responses == {}
    assert not hasattr(client, "_response_cache")


async def test_broadcast_collects_simultaneous_responses(
    loopback_client: tuple[MarstekUDPClient, socket.socket, list[int]],
) -> None:
    """Responses sharing a request ID are all collected, then released."""
    client, device, sent_ids = loopback_client
    client.set_broadcast_addresses(["192.168.1.255"])

    broadcast = asyncio.create_task(
        client.send_broadcast_request(discover(), timeout=0.2)
    )
    await _wait_for(lambda: sent_ids)
    for ip in ("192.168.1.10", "192.168.1.11"):
        device.send(json.dumps({"id": sent_ids[0], "result": {"ip": ip}}).encode())

    responses = await broadcast
    assert [response["result"]["ip"] for response in responses] == [
        "192.168.1.10",
        "192.168.1.11",
    ]

    device.send(json.dumps({"id": sent_ids[0], "result": {}}).encode())
    await asyncio.sleep(0.05)
    assert client._broadcast_responses == {}


async def test_get_device_status_raises_when_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent device raises MarstekNoResponse; recorded results are unchanged."""
    client = MarstekUDPClient()
    answer = True

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if not answer:
            raise TimeoutError
        if json.loads(args[0])["method"] == "PV.GetStatus":
            return {"error": {"code": -32601}}
        return {"result": {"bat_soc": 90, "mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert client.get_last_results("192.168.1.10") == {
        "ES.GetStatus": {"bat_soc": 90, "mode": 0},
        "ES.GetMode": {"bat_soc": 90, "mode": 0},
    }

    answer = False
    with pytest.raises(MarstekNoResponse):
        await client.get_device_status("192.168.1.10", delay_ms=0)
    # The silent call added nothing to the recorded results.
    assert client.get_last_results("192.168.1.10") == {
        "ES.GetStatus": {"bat_soc": 90, "mode": 0},
        "ES.GetMode": {"bat_soc": 90, "mode": 0},
    }


async def test_get_device_status_records_partial_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Methods that stay silent drop out of the recorded results."""
    client = MarstekUDPClient()
    es_status_answer = True

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if not es_status_answer and json.loads(args[0])["method"] == "ES.GetStatus":
            raise TimeoutError
        if json.loads(args[0])["method"] == "PV.GetStatus":
            return {"error": {"code": -32601}}
        return {"result": {"bat_soc": 90, "mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert set(client.get_last_results("192.168.1.10")) == {
        "ES.GetStatus",
        "ES.GetMode",
    }

    es_status_answer = False
    await client.get_device_status("192.168.1.10", delay_ms=0)
    # Only the methods that answered this poll are recorded.
    assert set(client.get_last_results("192.168.1.10")) == {"ES.GetMode"}


async def test_get_device_status_skips_pv_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_pv=False never sends PV.GetStatus."""
    client = MarstekUDPClient()
    pv_requests = 0

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal pv_requests
        if json.loads(args[0])["method"] == "PV.GetStatus":
            pv_requests += 1
        return {"result": {"bat_soc": 90, "mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=0, include_pv=False)

    assert pv_requests == 0


async def test_is_pv_status_supported_reflects_device_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PV support is None until the device answers or refuses PV.GetStatus."""
    client = MarstekUDPClient()
    pv_answer = True

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if json.loads(args[0])["method"] == "PV.GetStatus":
            if pv_answer:
                return {"result": {"pv1_power": 10}}
            return {"error": {"code": -32601}}
        return {"result": {"bat_soc": 90, "mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    assert client.is_pv_status_supported("192.168.1.10") is None

    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert client.is_pv_status_supported("192.168.1.10") is True

    pv_answer = False
    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert client.is_pv_status_supported("192.168.1.10") is False
