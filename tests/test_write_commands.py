"""Tests for the configuration (write) commands."""

from __future__ import annotations

import json
from typing import Any

import pytest

from aiomarstek import (
    MarstekManualSlot,
    MarstekModeConfig,
    MarstekPassiveConfig,
    MarstekSetError,
    MarstekUDPClient,
)


def _set_result_response() -> dict[str, Any]:
    """Build a set-command response with a successful set_result."""
    return {"id": 1, "src": "Venus-mac", "result": {"id": 0, "set_result": True}}


@pytest.fixture
def recording_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MarstekUDPClient, list[dict[str, Any]]]:
    """A client whose set commands are intercepted and recorded."""
    client = MarstekUDPClient()
    sent: list[dict[str, Any]] = []

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        sent.append(json.loads(message))
        return _set_result_response()

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )
    return client, sent


async def test_set_device_mode_manual(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """set_device_mode sends the ES.SetMode payload and checks set_result."""
    client, sent = recording_client

    config = MarstekModeConfig(
        mode="manual",
        manual_slots=(
            MarstekManualSlot(
                time_num=1,
                start_time="08:30",
                end_time="20:30",
                week_set=127,
                power=100,
            ),
            MarstekManualSlot(
                time_num=2,
                start_time="21:00",
                end_time="23:00",
                week_set=3,
                power=250,
                enable=False,
                manual_set=2,
            ),
        ),
    )
    await client.set_device_mode("192.168.1.10", config)

    assert sent[0]["method"] == "ES.SetMode"
    assert sent[0]["params"]["id"] == 0
    assert sent[0]["params"]["config"] == {
        "mode": "Manual",
        "manual_cfg": [
            {
                "time_num": 1,
                "start_time": "08:30",
                "end_time": "20:30",
                "week_set": 127,
                "power": 100,
                "enable": 1,
            },
            {
                "time_num": 2,
                "start_time": "21:00",
                "end_time": "23:00",
                "week_set": 3,
                "power": 250,
                "enable": 0,
                "manual_set": 2,
            },
        ],
    }


async def test_set_device_mode_simple_modes(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """Auto, AI, Passive and UPS configs map to their documented payloads."""
    client, sent = recording_client

    await client.set_device_mode("192.168.1.10", MarstekModeConfig(mode="auto"))
    await client.set_device_mode("192.168.1.10", MarstekModeConfig(mode="ai"))
    await client.set_device_mode(
        "192.168.1.10",
        MarstekModeConfig(
            mode="passive", passive=MarstekPassiveConfig(power=100, cd_time=300)
        ),
    )
    await client.set_device_mode("192.168.1.10", MarstekModeConfig(mode="ups"))
    await client.set_device_mode(
        "192.168.1.10", MarstekModeConfig(mode="ups", ups_enabled=False)
    )

    configs = [request["params"]["config"] for request in sent]
    assert configs == [
        {"mode": "Auto", "auto_cfg": {"enable": 1}},
        {"mode": "AI", "ai_cfg": {"enable": 1}},
        {"mode": "Passive", "passive_cfg": {"power": 100, "cd_time": 300}},
        {"mode": "UPS", "ups_cfg": {"enable": 1}},
        {"mode": "UPS", "ups_cfg": {"enable": 0}},
    ]


async def test_set_commands_reject_failed_set_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A false set_result raises MarstekSetError."""
    client = MarstekUDPClient()

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return {"id": 1, "result": {"id": 0, "set_result": False}}

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.set_dod("192.168.1.10", 50)


async def test_set_commands_accept_documented_set_result_typo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented "ture" spelling is accepted as success."""
    client = MarstekUDPClient()

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return {"id": 1, "result": {"id": 0, "set_result": "ture"}}

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    await client.set_dod("192.168.1.10", 50)


async def test_set_commands_raise_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent device raises MarstekSetError instead of TimeoutError."""
    client = MarstekUDPClient()

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        raise TimeoutError

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.set_led("192.168.1.10", True)


async def test_set_commands_raise_on_missing_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response without a result object raises MarstekSetError."""
    client = MarstekUDPClient()

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return {"id": 1}

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.set_bluetooth_advertising("192.168.1.10", True)


async def test_set_dod_validates_range() -> None:
    """Discharge depth outside 30-88 raises ValueError before sending."""
    client = MarstekUDPClient()

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no request should be sent")

    client._async_send_set_command = fail  # type: ignore[method-assign]

    with pytest.raises(ValueError):
        await client.set_dod("192.168.1.10", 29)
    with pytest.raises(ValueError):
        await client.set_dod("192.168.1.10", 89)


async def test_set_power_version_validates_value() -> None:
    """Unknown power versions raise ValueError before sending."""
    client = MarstekUDPClient()

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no request should be sent")

    client._async_send_set_command = fail  # type: ignore[method-assign]

    with pytest.raises(ValueError):
        await client.set_power_version("192.168.1.10", 1000)


async def test_set_ble_adv_inverts_enable(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """Ble.Adv sends enable=0 to turn advertising on."""
    client, sent = recording_client

    await client.set_bluetooth_advertising("192.168.1.10", True)
    await client.set_bluetooth_advertising("192.168.1.10", False)

    assert sent[0]["method"] == "Ble.Adv"
    assert sent[0]["params"] == {"id": 0, "enable": 0}
    assert sent[1]["params"] == {"id": 0, "enable": 1}


async def test_set_led_maps_state(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """Led.Ctrl maps on/off to state 1/0."""
    client, sent = recording_client

    await client.set_led("192.168.1.10", True)
    await client.set_led("192.168.1.10", False)

    assert sent[0]["method"] == "Led.Ctrl"
    assert sent[0]["params"] == {"id": 0, "state": 1}
    assert sent[1]["params"] == {"id": 0, "state": 0}


async def test_factory_reset_sends_keep_data_type(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """Reset.Factory sends type 2 to keep accumulated data."""
    client, sent = recording_client

    await client.factory_reset("192.168.1.10")
    await client.factory_reset("192.168.1.10", keep_accumulated_data=True)

    assert sent[0]["method"] == "Reset.Factory"
    assert sent[0]["params"] == {"id": 0, "type": 1}
    assert sent[1]["params"] == {"id": 0, "type": 2}


async def test_set_commands_pause_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set commands run with polling paused for the target device."""
    client = MarstekUDPClient()
    paused_states: list[bool] = []

    async def fake_send_request(
        message: str,
        target_ip: str,
        *args: Any,
        quiet_on_timeout: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        paused_states.append(client.is_polling_paused(target_ip))
        return _set_result_response()

    monkeypatch.setattr(client, "send_request", fake_send_request)

    await client.set_dod("192.168.1.10", 50)

    assert paused_states == [True]
    assert client.is_polling_paused("192.168.1.10") is False
