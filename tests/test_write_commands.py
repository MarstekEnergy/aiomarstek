"""Tests for the configuration (write) commands."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aiomarstek import (
    WRITE_MAX_RETRIES,
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

    # Manual mode is sent one slot per ES.SetMode, each as a single-object
    # manual_cfg (the documented form), not as one array-valued command.
    assert [s["method"] for s in sent] == ["ES.SetMode", "ES.SetMode"]
    assert sent[0]["params"]["id"] == 0
    assert sent[0]["params"]["config"] == {
        "mode": "Manual",
        "manual_cfg": {
            "time_num": 1,
            "start_time": "08:30",
            "end_time": "20:30",
            "week_set": 127,
            "power": 100,
            "enable": 1,
        },
    }
    assert sent[1]["params"]["config"]["manual_cfg"] == {
        "time_num": 2,
        "start_time": "21:00",
        "end_time": "23:00",
        "week_set": 3,
        "power": 250,
        "enable": 0,
        "manual_set": 2,
    }


async def test_set_manual_schedule_sends_each_slot_separately(
    recording_client: tuple[MarstekUDPClient, list[dict[str, Any]]],
) -> None:
    """set_manual_schedule sends one single-object SetMode per slot."""
    client, sent = recording_client

    await client.set_manual_schedule(
        "192.168.1.10",
        [
            MarstekManualSlot(
                time_num=0,
                start_time="00:00",
                end_time="06:00",
                week_set=127,
                power=800,
                manual_set=1,
            ),
            MarstekManualSlot(
                time_num=1,
                start_time="18:00",
                end_time="21:00",
                week_set=127,
                power=1500,
                manual_set=2,
            ),
        ],
    )

    assert [s["method"] for s in sent] == ["ES.SetMode", "ES.SetMode"]
    assert sent[0]["params"]["config"]["manual_cfg"] == {
        "time_num": 0,
        "start_time": "00:00",
        "end_time": "06:00",
        "week_set": 127,
        "power": 800,
        "enable": 1,
        "manual_set": 1,
    }
    assert sent[1]["params"]["config"]["manual_cfg"]["time_num"] == 1
    assert sent[1]["params"]["config"]["manual_cfg"]["power"] == 1500


async def test_manual_schedule_is_atomic_against_interleaving_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write queued while a schedule runs waits for all slots to finish.

    Holding the per-device write lock for the whole schedule means a mode
    change cannot land between two slots (which could flip the device out of
    Manual while later slots still apply the old schedule).
    """
    client = MarstekUDPClient()
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[dict[str, Any]] = []

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        sent.append(json.loads(message))
        if len(sent) == 1:
            started.set()
            await release.wait()
        return _set_result_response()

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    slots = [
        MarstekManualSlot(
            time_num=0,
            start_time="00:00",
            end_time="06:00",
            week_set=127,
            power=800,
            manual_set=1,
        ),
        MarstekManualSlot(
            time_num=1,
            start_time="18:00",
            end_time="21:00",
            week_set=127,
            power=1500,
            manual_set=2,
        ),
    ]
    schedule = asyncio.create_task(client.set_manual_schedule("1.2.3.4", slots))
    await started.wait()  # slot 0 in flight, schedule holds the write lock

    # Queue a mode change while the schedule holds the lock.
    mode_change = asyncio.create_task(
        client.set_device_mode("1.2.3.4", MarstekModeConfig(mode="ai"))
    )
    await asyncio.sleep(0)  # let it queue on the write lock

    # Only slot 0 sent so far; the mode change did NOT interleave.
    assert len(sent) == 1
    assert "manual_cfg" in sent[0]["params"]["config"]

    release.set()
    await asyncio.gather(schedule, mode_change)

    # Both slots sent first (under one lock), then the mode change last.
    assert len(sent) == 3
    assert "manual_cfg" in sent[0]["params"]["config"]
    assert "manual_cfg" in sent[1]["params"]["config"]
    assert "manual_cfg" not in sent[2]["params"]["config"]


async def test_set_manual_schedule_requires_at_least_one_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty manual schedule is rejected before touching the device."""
    client = MarstekUDPClient()
    with pytest.raises(ValueError):
        await client.set_manual_schedule("192.168.1.10", [])


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


async def test_factory_reset_is_not_retried_after_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent reset is not resent: a lost reply must not reset twice."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise TimeoutError

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.factory_reset("192.168.1.10")

    assert calls == 1


async def test_factory_reset_retries_when_caller_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retry_on_timeout=True restores the generic write retry behaviour."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise TimeoutError

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.factory_reset("192.168.1.10", retry_on_timeout=True)

    assert calls == WRITE_MAX_RETRIES + 1


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


async def test_write_command_retries_once_after_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent first write attempt is retried; success clears the error."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return _set_result_response()

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    await client.set_led("192.168.1.10", True)

    assert calls == 2


async def test_write_command_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that stays silent past the retries surfaces as an error."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise TimeoutError

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.set_led("192.168.1.10", True)

    assert calls == WRITE_MAX_RETRIES + 1


async def test_write_command_does_not_retry_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that answers but rejects the command is not retried."""
    client = MarstekUDPClient()
    calls = 0

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"id": 1, "result": {"id": 0, "set_result": False}}

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    with pytest.raises(MarstekSetError):
        await client.set_led("192.168.1.10", True)

    assert calls == 1


async def test_write_skips_superseded_queued_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write queued behind an executing one is dropped when a newer wins."""
    client = MarstekUDPClient()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(json.loads(message)["method"])
        if len(calls) == 1:
            started.set()
            await release.wait()
        return _set_result_response()

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    write1 = asyncio.create_task(client.set_led("1.2.3.4", True))
    await started.wait()  # write1 is in flight, holding the write lock
    write2 = asyncio.create_task(client.set_led("1.2.3.4", False))
    write3 = asyncio.create_task(client.set_led("1.2.3.4", True))
    await asyncio.sleep(0)  # let write2 and write3 queue on the write lock
    release.set()

    await asyncio.gather(write1, write2, write3)

    # Only the first (already running) and the latest actually sent; the
    # write queued in between was superseded and skipped.
    assert calls == ["Led.Ctrl", "Led.Ctrl"]


async def test_write_does_not_drop_different_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued write of one setting is not dropped by a newer write of another.

    Coalescing is per (device, setting): an LED write queued behind an
    in-flight DOD write must still run even if a newer DOD write arrives
    while it waits -- LED and DOD do not replace each other.
    """
    client = MarstekUDPClient()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def fake_send_request_with_polling_control(
        message: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(json.loads(message)["method"])
        if len(calls) == 1:
            started.set()
            await release.wait()
        return _set_result_response()

    monkeypatch.setattr(
        client,
        "send_request_with_polling_control",
        fake_send_request_with_polling_control,
    )

    # write1 = DOD, holds the lock; write2 = LED queues behind it; then a
    # newer DOD write (write3) arrives. The LED write must NOT be skipped.
    write1 = asyncio.create_task(client.set_dod("1.2.3.4", 50))
    await started.wait()
    write2 = asyncio.create_task(client.set_led("1.2.3.4", True))
    write3 = asyncio.create_task(client.set_dod("1.2.3.4", 60))
    await asyncio.sleep(0)  # let write2 and write3 queue on the write lock
    release.set()

    await asyncio.gather(write1, write2, write3)

    # All three settings were sent; the LED write was not dropped.
    # Order follows the write lock's FIFO: DOD (running) -> LED (queued
    # first) -> DOD (queued second).
    assert calls == ["DOD.SET", "Led.Ctrl", "DOD.SET"]
