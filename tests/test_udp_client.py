"""Tests for the UDP client."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
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
                    "soc": 90,
                    "charg_flag": True,
                    "dischrg_flag": False,
                    "bat_temp": 25.5,
                    "bat_capacity": 256.0,
                    "rated_capacity": 2560.0,
                }
            },
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
            ongrid_power=100,
            battery_temperature=25.5,
            battery_remaining_capacity=256.0,
            battery_rated_capacity=2560.0,
            charge_permission=True,
            discharge_permission=False,
            total_pv_energy=2810,
            pv1_power=42.5,
            pv1_voltage=10.5,
            pv1_current=4.25,
            pv1_state="working",
        )
    )
    assert [request["method"] for request in requests] == [
        "ES.GetStatus",
        "ES.GetMode",
        "Bat.GetStatus",
        "PV.GetStatus",
    ]


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
            {"result": {"soc": 90}},
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
            ongrid_power=-100,
        )
    )
    assert [(request["method"], request["params"]) for request in requests] == [
        ("ES.GetStatus", {"id": 0}),
        ("ES.GetMode", {"id": 1}),
        ("Bat.GetStatus", {"id": 0}),
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
            {"result": {"soc": 75}},
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
            ongrid_power=50,
        )
    )
    assert [(request["method"], request["params"]) for request in requests] == [
        ("ES.GetStatus", {"id": 0}),
        ("ES.GetMode", {"id": 1}),
        ("ES.GetMode", {"id": 0}),
        ("Bat.GetStatus", {"id": 0}),
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
        "Bat.GetStatus",
        "PV.GetStatus",
        "ES.GetStatus",
        "ES.GetMode",
        "Bat.GetStatus",
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
    client = MarstekUDPClient(verify_source_ip=False)
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
        "Bat.GetStatus": {"bat_soc": 90, "mode": 0},
    }

    answer = False
    with pytest.raises(MarstekNoResponse):
        await client.get_device_status("192.168.1.10", delay_ms=0)
    # The silent call added nothing to the recorded results.
    assert client.get_last_results("192.168.1.10") == {
        "ES.GetStatus": {"bat_soc": 90, "mode": 0},
        "ES.GetMode": {"bat_soc": 90, "mode": 0},
        "Bat.GetStatus": {"bat_soc": 90, "mode": 0},
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
        "Bat.GetStatus",
    }

    es_status_answer = False
    await client.get_device_status("192.168.1.10", delay_ms=0)
    # Only the methods that answered this poll are recorded.
    assert set(client.get_last_results("192.168.1.10")) == {
        "ES.GetMode",
        "Bat.GetStatus",
    }


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


async def test_get_device_status_throttles_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WiFi, BLE and EM are only queried every EXTRAS_INTERVAL polls."""
    from aiomarstek import EXTRAS_INTERVAL

    client = MarstekUDPClient()
    requests: list[str] = []

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request = json.loads(args[0])
        requests.append(request["method"])
        if request["method"] == "PV.GetStatus":
            return {"error": {"code": -32601}}
        if request["method"] == "Wifi.GetStatus":
            return {"result": {"rssi": -59, "ssid": "home"}}
        if request["method"] == "BLE.GetStatus":
            return {"result": {"state": "connect"}}
        if request["method"] == "EM.GetStatus":
            return {"result": {"ct_state": 1, "total_power": 120}}
        if request["method"] == "ES.GetStatus":
            return {"result": {"bat_soc": 90}}
        return {"result": {"mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    for _ in range(EXTRAS_INTERVAL):
        await client.get_device_status("192.168.1.10", delay_ms=0)

    assert requests.count("Wifi.GetStatus") == 1
    assert requests.count("BLE.GetStatus") == 1
    assert requests.count("EM.GetStatus") == 1
    assert requests.count("Bat.GetStatus") == EXTRAS_INTERVAL

    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert requests.count("Wifi.GetStatus") == 1


async def test_get_device_status_merges_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WiFi, BLE and EM responses populate the normalized status."""
    from aiomarstek import EXTRAS_INTERVAL

    client = MarstekUDPClient()
    client._extras_age["192.168.1.10"] = EXTRAS_INTERVAL - 1

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request = json.loads(args[0])
        if request["method"] == "PV.GetStatus":
            return {"error": {"code": -32601}}
        if request["method"] == "Wifi.GetStatus":
            return {
                "result": {
                    "ssid": "home",
                    "rssi": -59,
                    "sta_ip": "192.168.1.10",
                    "sta_gate": "192.168.1.1",
                    "sta_mask": "255.255.255.0",
                    "sta_dns": "192.168.1.1",
                }
            }
        if request["method"] == "BLE.GetStatus":
            return {"result": {"state": "connect"}}
        if request["method"] == "EM.GetStatus":
            return {
                "result": {
                    "ct_state": 1,
                    "a_power": 10,
                    "b_power": 20,
                    "c_power": 30,
                    "total_power": 60,
                    "input_energy": 3086320,
                    "output_energy": 4487510,
                }
            }
        if request["method"] == "ES.GetStatus":
            return {"result": {"bat_soc": 90}}
        return {"result": {"mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    status = await client.get_device_status("192.168.1.10", delay_ms=0)

    assert status.wifi_ssid == "home"
    assert status.wifi_rssi == -59
    assert status.wifi_ip == "192.168.1.10"
    assert status.wifi_gateway == "192.168.1.1"
    assert status.wifi_netmask == "255.255.255.0"
    assert status.wifi_dns == "192.168.1.1"
    assert status.ble_state == "connect"
    assert status.ct_state is True
    assert status.a_power == 10
    assert status.b_power == 20
    assert status.c_power == 30
    assert status.total_power == 60
    assert status.ct_input_energy == 308632.0
    assert status.ct_output_energy == 448751.0


async def test_get_device_status_marks_unsupported_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extras answering "method not found" are skipped afterwards."""
    client = MarstekUDPClient()
    client._extras_age["192.168.1.10"] = 99  # Force the extras cycle.
    requests: list[str] = []

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request = json.loads(args[0])
        requests.append(request["method"])
        if request["method"] in ("Wifi.GetStatus", "BLE.GetStatus", "EM.GetStatus"):
            return {"error": {"code": -32601, "message": "Method not found"}}
        if request["method"] == "PV.GetStatus":
            return {"error": {"code": -32601}}
        if request["method"] == "ES.GetStatus":
            return {"result": {"bat_soc": 90}}
        return {"result": {"mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    status = await client.get_device_status("192.168.1.10", delay_ms=0)
    assert status.wifi_rssi is None
    assert status.ble_state is None

    client._extras_age["192.168.1.10"] = 99  # Due again on the next poll.
    await client.get_device_status("192.168.1.10", delay_ms=0)
    assert requests.count("Wifi.GetStatus") == 1
    assert requests.count("BLE.GetStatus") == 1
    assert requests.count("EM.GetStatus") == 1


async def test_get_device_status_skips_battery_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_battery=False never sends Bat.GetStatus."""
    client = MarstekUDPClient()
    battery_requests = 0

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal battery_requests
        if json.loads(args[0])["method"] == "Bat.GetStatus":
            battery_requests += 1
        return {"result": {"bat_soc": 90, "mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=0, include_battery=False)

    assert battery_requests == 0


async def _drive_listen(
    client: MarstekUDPClient,
    packets: list[tuple[bytes, tuple[str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run _listen_for_responses over a fixed packet list, then stop it."""
    client._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    iterator = iter(packets)

    async def fake_recvfrom(
        sock: object, bufsize: int
    ) -> tuple[bytes, tuple[str, int]]:
        try:
            return next(iterator)
        except StopIteration:
            raise asyncio.CancelledError from None

    monkeypatch.setattr(
        asyncio.get_running_loop(), "sock_recvfrom", fake_recvfrom, raising=False
    )
    try:
        await client._listen_for_responses()
    finally:
        client._socket.close()
        client._socket = None


async def test_listen_drops_non_utf8_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-UTF-8 packet is dropped without killing the listener."""
    client = MarstekUDPClient(verify_source_ip=False)
    await _drive_listen(client, [(b"\xff\xfe\x00", ("1.2.3.4", 9))], monkeypatch)
    # Reaching here means the listener did not crash on the bad packet.


async def test_listen_drops_malformed_json_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid JSON degrades to a raw payload without killing the listener."""
    client = MarstekUDPClient(verify_source_ip=False)
    await _drive_listen(client, [(b"not json", ("1.2.3.4", 9))], monkeypatch)


async def test_unicast_response_from_wrong_source_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A unicast reply from a host other than the target is not resolved."""
    client = MarstekUDPClient(verify_source_ip=True)
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    client._pending_requests[42] = (future, "1.2.3.4")

    await _drive_listen(
        client,
        [(json.dumps({"id": 42, "result": {}}).encode(), ("9.9.9.9", 9))],
        monkeypatch,
    )

    assert not future.done()
    assert 42 in client._pending_requests


async def test_unicast_response_from_right_source_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A unicast reply from the target host resolves the waiting future."""
    client = MarstekUDPClient(verify_source_ip=True)
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    client._pending_requests[42] = (future, "1.2.3.4")

    await _drive_listen(
        client,
        [
            (
                json.dumps({"id": 42, "result": {"ok": 1}}).encode(),
                ("1.2.3.4", 9),
            )
        ],
        monkeypatch,
    )

    assert future.result() == {"id": 42, "result": {"ok": 1}}
    assert 42 not in client._pending_requests


async def test_get_device_status_raises_when_polling_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused device skips the poll and raises MarstekNoResponse."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"result": {}}

    monkeypatch.setattr(client, "send_request", fake_send_request)
    await client.pause_polling("1.2.3.4")

    with pytest.raises(MarstekNoResponse):
        await client.get_device_status("1.2.3.4", delay_ms=0)

    assert calls == 0


async def test_polling_pause_count_overlaps() -> None:
    """Overlapping pauses only resume once every one has finished."""
    client = MarstekUDPClient()
    assert not client.is_polling_paused("1.2.3.4")
    await client.pause_polling("1.2.3.4")
    await client.pause_polling("1.2.3.4")
    assert client.is_polling_paused("1.2.3.4")
    await client.resume_polling("1.2.3.4")
    assert client.is_polling_paused("1.2.3.4")
    await client.resume_polling("1.2.3.4")
    assert not client.is_polling_paused("1.2.3.4")


async def test_discover_devices_raises_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broadcast failure is raised, not returned as an empty list."""
    client = MarstekUDPClient()

    async def failing_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise OSError("boom")

    monkeypatch.setattr(client, "send_broadcast_request", failing_broadcast)

    with pytest.raises(OSError):
        await client.discover_devices(use_cache=False)

    assert client.get_discovery_cache() is None


async def test_discover_devices_caches_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine empty result is cached so the next call does not re-broadcast."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(client, "send_broadcast_request", fake_broadcast)

    first = await client.discover_devices()
    second = await client.discover_devices()

    assert first == []
    assert second == []
    assert calls == 1


def _per_poll_status(responses: list[dict[str, Any] | None]) -> Any:
    """Fake send_request serving one ES.GetStatus response per poll.

    A None entry makes every request of that poll time out, so the poll
    raises MarstekNoResponse without updating streaks. PV.GetStatus always
    reports unsupported so it is not queried again.
    """
    iterator = iter(responses)
    latest: dict[str, Any] = {}
    timed_out = False

    async def fake_send_request(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal timed_out
        method = json.loads(message)["method"]
        if method == "ES.GetStatus":
            nxt = next(iterator)
            timed_out = nxt is None
            if timed_out:
                raise TimeoutError
            latest.clear()
            latest.update(nxt)
            return {"result": latest}
        if timed_out:
            raise TimeoutError
        if method == "PV.GetStatus":
            return {"error": {"code": -32601}}
        return {"result": latest}

    return fake_send_request


async def test_quiet_gap_holds_request_after_recent_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured gap delays the next request to a recently talking device."""
    client = MarstekUDPClient()
    client._min_request_gap = 2.5
    loop = asyncio.get_running_loop()
    client._last_response_at["1.2.3.4"] = loop.time() - 1.0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await client._wait_for_quiet_period("1.2.3.4")

    assert sleeps == [pytest.approx(1.5, abs=0.05)]


async def test_quiet_gap_skipped_without_recent_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No wait for a device that never answered, or answered long ago."""
    client = MarstekUDPClient()
    client._min_request_gap = 2.5
    slept = False

    async def fake_sleep(delay: float) -> None:
        nonlocal slept
        slept = True

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await client._wait_for_quiet_period("1.2.3.4")  # never responded
    client._last_response_at["1.2.3.4"] = asyncio.get_running_loop().time() - 10.0
    await client._wait_for_quiet_period("1.2.3.4")  # long-ago response
    client._min_request_gap = 0.0
    client._last_response_at["1.2.3.4"] = asyncio.get_running_loop().time() - 0.1
    await client._wait_for_quiet_period("1.2.3.4")  # gap disabled

    assert slept is False


async def test_listener_records_last_response_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every received packet refreshes the per-device last-response time."""
    client = MarstekUDPClient(verify_source_ip=False)
    loop = asyncio.get_running_loop()

    await _drive_listen(
        client,
        [(json.dumps({"id": 7, "result": {}}).encode(), ("1.2.3.4", 9))],
        monkeypatch,
    )

    assert client._last_response_at["1.2.3.4"] <= loop.time()


async def test_extras_queries_are_spaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WiFi, BLE and EM queries are spaced by the poll delay."""
    client = MarstekUDPClient()
    client._extras_age["192.168.1.10"] = 99  # Force the extras cycle.
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def fake_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        request = json.loads(args[0])
        method = request["method"]
        if method == "Wifi.GetStatus":
            return {"result": {"rssi": -59, "ssid": "home"}}
        if method == "BLE.GetStatus":
            return {"result": {"state": "connect"}}
        if method == "EM.GetStatus":
            return {"result": {"ct_state": 1, "total_power": 120}}
        if method == "ES.GetStatus":
            return {"result": {"bat_soc": 90}}
        return {"result": {"mode": 0}}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.get_device_status("192.168.1.10", delay_ms=2000, include_pv=False)

    # One delay between each pair of queries: es_status->es_mode,
    # es_mode->battery, battery->extras, wifi->ble and ble->em.
    assert sleeps.count(2.0) == 5


async def test_status_smoothing_holds_soc_spike_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single 0% SOC reading after a high level is held, then cleared."""
    client = MarstekUDPClient()
    monkeypatch.setattr(
        client,
        "send_request",
        _per_poll_status(
            [
                {"bat_soc": 50, "mode": 0},
                {"bat_soc": 0, "mode": 0},
                {"bat_soc": 50, "mode": 0},
            ]
        ),
    )

    first = await client.get_device_status("1.2.3.4", delay_ms=0)
    second = await client.get_device_status("1.2.3.4", delay_ms=0)
    third = await client.get_device_status("1.2.3.4", delay_ms=0)

    assert first.battery_soc == 50
    assert second.battery_soc == 50  # spike held
    assert third.battery_soc == 50
    assert client._zero_soc_polls["1.2.3.4"] == 0  # recovery cleared the streak


async def test_status_smoothing_accepts_persistent_zero_soc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0% is accepted once it persists past the escape threshold."""
    client = MarstekUDPClient()
    monkeypatch.setattr(
        client,
        "send_request",
        _per_poll_status(
            [
                {"bat_soc": 50, "mode": 0},
                {"bat_soc": 0, "mode": 0},
                {"bat_soc": 0, "mode": 0},
                {"bat_soc": 0, "mode": 0},
                {"bat_soc": 0, "mode": 0},
            ]
        ),
    )

    results = [await client.get_device_status("1.2.3.4", delay_ms=0) for _ in range(5)]
    assert [r.battery_soc for r in results] == [50, 50, 50, 50, 0]


async def test_status_smoothing_holds_energy_dip_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monotonic counter dip is held, then accepted past the threshold."""
    client = MarstekUDPClient()
    monkeypatch.setattr(
        client,
        "send_request",
        _per_poll_status(
            [
                {"total_pv_energy": 1000},
                {"total_pv_energy": 5},
                {"total_pv_energy": 5},
                {"total_pv_energy": 5},
            ]
        ),
    )

    results = [await client.get_device_status("1.2.3.4", delay_ms=0) for _ in range(4)]
    # total_pv_energy is scaled by 10: 1000 -> 10000, 5 -> 50.
    assert results[0].total_pv_energy == 10000
    assert results[1].total_pv_energy == 10000  # dip held
    assert results[2].total_pv_energy == 10000  # dip still held
    assert results[3].total_pv_energy == 50  # accepted past the threshold


async def test_status_smoothing_isolates_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each device keeps its own streak; one device's spike does not affect another."""
    client = MarstekUDPClient()
    polls = {
        "10.0.0.1": iter([{"bat_soc": 50, "mode": 0}, {"bat_soc": 0, "mode": 0}]),
        "10.0.0.2": iter([{"bat_soc": 80, "mode": 0}, {"bat_soc": 0, "mode": 0}]),
    }
    latest: dict[str, dict[str, Any]] = {"10.0.0.1": {}, "10.0.0.2": {}}

    async def fake_send_request(
        message: str, target_ip: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        method = json.loads(message)["method"]
        if method == "PV.GetStatus":
            return {"error": {"code": -32601}}
        if method == "ES.GetStatus":
            latest[target_ip].clear()
            latest[target_ip].update(next(polls[target_ip]))
        return {"result": latest[target_ip]}

    monkeypatch.setattr(client, "send_request", fake_send_request)

    a1 = await client.get_device_status("10.0.0.1", delay_ms=0)
    b1 = await client.get_device_status("10.0.0.2", delay_ms=0)
    a2 = await client.get_device_status("10.0.0.1", delay_ms=0)
    b2 = await client.get_device_status("10.0.0.2", delay_ms=0)

    assert a1.battery_soc == 50
    assert b1.battery_soc == 80
    assert a2.battery_soc == 50  # A's spike held
    assert b2.battery_soc == 80  # B's spike held independently
    assert client._zero_soc_polls["10.0.0.1"] == 1
    assert client._zero_soc_polls["10.0.0.2"] == 1


async def test_status_smoothing_survives_failed_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed poll does not clear the streak; the next spike is still held."""
    client = MarstekUDPClient()
    monkeypatch.setattr(
        client,
        "send_request",
        _per_poll_status([{"bat_soc": 50, "mode": 0}, None, {"bat_soc": 0, "mode": 0}]),
    )

    first = await client.get_device_status("1.2.3.4", delay_ms=0)
    with pytest.raises(MarstekNoResponse):
        await client.get_device_status("1.2.3.4", delay_ms=0)
    third = await client.get_device_status("1.2.3.4", delay_ms=0)

    assert first.battery_soc == 50
    assert third.battery_soc == 50  # spike held; the silent poll kept the streak
    assert client._zero_soc_polls["1.2.3.4"] == 1


class _FakeSock:
    """Minimal socket double that records socket options and bind."""

    def __init__(self) -> None:
        self.opts: dict[tuple[int, int], int] = {}
        self.bound: tuple[str, int] | None = None

    def setsockopt(self, level: int, opt: int, val: int) -> None:
        self.opts[(level, opt)] = val

    def setblocking(self, flag: bool) -> None:
        pass

    def bind(self, addr: tuple[str, int]) -> None:
        self.bound = addr

    def getsockname(self) -> tuple[str, int]:
        return self.bound or ("0.0.0.0", 30000)

    def close(self) -> None:
        pass


async def test_setup_does_not_enable_so_reuseaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unicast socket must NOT set SO_REUSEADDR.

    SO_REUSEADDR would let another socket also bind 0.0.0.0:30000 and
    silently steal this client's unicast replies (the device replies to the
    source port, 30000), producing "sends succeed but no replies" -- the
    field failure this guards against.
    """
    fake = _FakeSock()
    monkeypatch.setattr("aiomarstek.udp_client._SOCKET_FACTORY", lambda *a, **k: fake)
    client = MarstekUDPClient()
    try:
        await client.async_setup()
    finally:
        await client.async_cleanup()

    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR) not in fake.opts
    assert fake.opts[(socket.SOL_SOCKET, socket.SO_BROADCAST)] == 1
    assert fake.bound == ("0.0.0.0", 30000)


async def test_setup_bind_failure_leaves_no_socket_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bind failure must not leave a half-initialized socket.

    The socket is created/configured/bound in a local and only assigned to
    ``self._socket`` on success; on failure it is closed and ``self._socket``
    stays None so a later setup is not short-circuited by the guard.
    """
    bind_should_fail = True

    class _BindFailSock(_FakeSock):
        def bind(self, addr: tuple[str, int]) -> None:
            nonlocal bind_should_fail
            if bind_should_fail:
                bind_should_fail = False
                raise OSError("EADDRINUSE")
            super().bind(addr)

    monkeypatch.setattr(
        "aiomarstek.udp_client._SOCKET_FACTORY", lambda *a, **k: _BindFailSock()
    )
    client = MarstekUDPClient()

    with pytest.raises(OSError):
        await client.async_setup()

    # No half-initialized socket left -> a retry is not short-circuited.
    assert client._socket is None

    await client.async_setup()
    assert client._socket is not None
    assert client._socket.bound == ("0.0.0.0", 30000)

    await client.async_cleanup()


async def test_local_port_is_distinct_from_device_target_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``local_port`` binds the socket; ``port`` is the device target only.

    The device replies to the source port, so the local bind need not match
    the device's port; they are independent knobs.
    """
    fake = _FakeSock()
    monkeypatch.setattr("aiomarstek.udp_client._SOCKET_FACTORY", lambda *a, **k: fake)
    client = MarstekUDPClient(port=30001, local_port=40000)
    try:
        await client.async_setup()
    finally:
        await client.async_cleanup()

    # Bound to local_port, not to the device target port.
    assert fake.bound == ("0.0.0.0", 40000)
    assert client._port == 30001  # device target stays as configured
    assert client._local_port == 40000


async def test_listener_survives_dispatch_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error dispatching one packet does not kill the listener."""
    client = MarstekUDPClient(verify_source_ip=False)
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    client._pending_requests[42] = (future, "1.2.3.4")

    calls = 0
    real_handle = client._handle_response

    def flaky_handle(data: bytes, addr: tuple[str, int]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("simulated dispatch bug")
        real_handle(data, addr)

    monkeypatch.setattr(client, "_handle_response", flaky_handle)

    await _drive_listen(
        client,
        [
            (b"first", ("1.2.3.4", 9)),
            (json.dumps({"id": 42, "result": {"ok": 1}}).encode(), ("1.2.3.4", 9)),
        ],
        monkeypatch,
    )

    # The bad packet did not kill the loop; the second packet was dispatched.
    assert calls == 2
    assert future.result() == {"id": 42, "result": {"ok": 1}}


async def test_consecutive_recv_errors_trigger_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained OSError from recv indicates a bad socket and triggers a rebind.

    The listener must rebind via ``_reopen_socket`` (not ``_rebind_socket``),
    because it runs inside the listener task and awaiting itself would
    deadlock. Here the spy returns False so no fresh listener is spawned;
    the listener simply returns, proving the threshold path does not hang.
    """
    monkeypatch.setattr("aiomarstek.udp_client.RECV_ERROR_REBIND_THRESHOLD", 1)

    client = MarstekUDPClient()
    client._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    reopen_calls = 0

    async def spy_reopen() -> bool:
        nonlocal reopen_calls
        reopen_calls += 1
        return False  # pretend the rebind failed -> no new listener spawned

    monkeypatch.setattr(client, "_reopen_socket", spy_reopen)

    # Guard: _rebind_socket must NOT be called from the listener (it would
    # cancel and await self._listen_task == this task, deadlocking).
    rebind_calls = 0

    async def spy_rebind() -> None:
        nonlocal rebind_calls
        rebind_calls += 1

    monkeypatch.setattr(client, "_rebind_socket", spy_rebind)

    recv_count = 0

    async def failing_recvfrom(
        sock: object, bufsize: int
    ) -> tuple[bytes, tuple[str, int]]:
        nonlocal recv_count
        recv_count += 1
        raise OSError("boom")

    monkeypatch.setattr(
        asyncio.get_running_loop(), "sock_recvfrom", failing_recvfrom, raising=False
    )

    await client._listen_for_responses()

    assert recv_count == 1
    assert reopen_calls == 1
    assert rebind_calls == 0  # the listener rebound without self-awaiting
    client._socket.close()
    client._socket = None


async def test_listener_rebind_from_within_spawns_new_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError-threshold rebind from inside the listener hands off cleanly.

    A real rebind (``_reopen_socket`` succeeds) spawns a fresh listener task
    and the old one returns -- no deadlock, no self-await. The fresh listener
    blocks healthily on recvfrom (the normal "waiting for a reply" state).
    """
    monkeypatch.setattr("aiomarstek.udp_client.RECV_ERROR_REBIND_THRESHOLD", 1)

    client = MarstekUDPClient()
    client._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    reopened = asyncio.Event()

    async def spy_reopen() -> bool:
        reopened.set()
        return True  # pretend the socket was reopened successfully

    monkeypatch.setattr(client, "_reopen_socket", spy_reopen)

    call_count = 0

    async def recvfrom_once_then_block(
        sock: object, bufsize: int
    ) -> tuple[bytes, tuple[str, int]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("boom")  # old listener hits the threshold
        await asyncio.Event().wait()  # fresh listener blocks healthily
        return b"", ("", 0)

    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "sock_recvfrom",
        recvfrom_once_then_block,
        raising=False,
    )

    # Old listener: OSError -> rebind -> spawn fresh listener -> return.
    await client._listen_for_responses()
    # Let the freshly spawned listener start and reach its recvfrom.
    await asyncio.sleep(0.05)

    assert reopened.is_set()
    assert client._listen_task is not None
    assert not client._listen_task.done()  # fresh listener alive, blocked
    assert call_count == 2  # old (raised) + new (blocking)

    client._listen_task.cancel()
    with suppress(asyncio.CancelledError):
        await client._listen_task
    client._listen_task = None
    client._socket.close()
    client._socket = None


async def test_watchdog_restarts_dead_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener task that has exited is restarted by the watchdog."""
    fake = _FakeSock()
    monkeypatch.setattr("aiomarstek.udp_client._SOCKET_FACTORY", lambda *a, **k: fake)
    monkeypatch.setattr("aiomarstek.udp_client.LISTENER_WATCHDOG_INTERVAL", 0.01)

    async def blocking_recvfrom(
        sock: object, bufsize: int
    ) -> tuple[bytes, tuple[str, int]]:
        # A healthy listener simply blocks on recvfrom waiting for a reply.
        await asyncio.Event().wait()
        return b"", ("", 0)

    monkeypatch.setattr(
        asyncio.get_running_loop(), "sock_recvfrom", blocking_recvfrom, raising=False
    )

    client = MarstekUDPClient()
    await client.async_setup()

    # Stop the watchdog that async_setup started; we drive one iteration below.
    assert client._watchdog_task is not None
    client._watchdog_task.cancel()
    with suppress(asyncio.CancelledError):
        await client._watchdog_task
    client._watchdog_task = None
    # Drop any listener started during setup so the watchdog sees "no task".
    if client._listen_task and not client._listen_task.done():
        client._listen_task.cancel()
        with suppress(asyncio.CancelledError):
            await client._listen_task
    client._listen_task = None

    wd = asyncio.create_task(client._listener_watchdog())
    await asyncio.sleep(0.05)
    wd.cancel()
    with suppress(asyncio.CancelledError):
        await wd

    assert client._listen_task is not None
    assert not client._listen_task.done()

    await client.async_cleanup()


async def test_watchdog_rebounds_stuck_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener that received before but is now silent too long is rebound."""
    fake = _FakeSock()
    monkeypatch.setattr("aiomarstek.udp_client._SOCKET_FACTORY", lambda *a, **k: fake)
    monkeypatch.setattr("aiomarstek.udp_client.LISTENER_WATCHDOG_INTERVAL", 0.01)
    monkeypatch.setattr("aiomarstek.udp_client.LISTENER_STUCK_SILENCE", 0.02)

    client = MarstekUDPClient()
    await client.async_setup()

    # Simulate "was receiving earlier, now long silent" without running the
    # real listener task: mark prior traffic and an aged heartbeat.
    client._listener_ever_received = True
    client._listener_heartbeat = asyncio.get_running_loop().time() - 100.0
    # Pretend the listener is alive (done()==False) so the stuck branch runs.
    client._listen_task = asyncio.create_task(asyncio.Event().wait())

    rebind_calls = 0

    async def spy_rebind() -> None:
        nonlocal rebind_calls
        rebind_calls += 1

    monkeypatch.setattr(client, "_rebind_socket", spy_rebind)

    # Stop the setup watchdog; drive one watchdog tick manually.
    assert client._watchdog_task is not None
    client._watchdog_task.cancel()
    with suppress(asyncio.CancelledError):
        await client._watchdog_task
    wd = asyncio.create_task(client._listener_watchdog())
    await asyncio.sleep(0.05)
    wd.cancel()
    with suppress(asyncio.CancelledError):
        await wd

    assert rebind_calls == 1
    assert client._stuck_rebound_done is True

    client._listen_task.cancel()
    with suppress(asyncio.CancelledError):
        await client._listen_task
    await client.async_cleanup()
