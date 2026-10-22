"""Live device inspection script.

Run it with the device IP (or "discover" to broadcast-scan the LAN):

    .venv/bin/python examples/inspect_device.py 192.168.1.10
    .venv/bin/python examples/inspect_device.py discover

It only ever *reads* from the device; no configuration command is sent, so it
is safe to run against a production device. Use it to confirm the extended
status parsing (battery, WiFi, BLE, EM, CT phases) before publishing 0.2.0.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from aiomarstek import (
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_UPS,
    MarstekUDPClient,
)


def _value_to_display(value: Any) -> Any:
    if isinstance(value, float) and abs(value) >= 1000:
        return round(value, 1)
    return value


def _dump_status(status: Any, label: str) -> None:
    print(f"\n========== {label} ==========")
    if not is_dataclass(status):
        print(status)
        return
    fields = asdict(status)
    present = {k: _value_to_display(v) for k, v in fields.items() if v is not None}
    missing = [k for k, v in fields.items() if v is None]
    for key, value in sorted(present.items()):
        print(f"  {key:32} = {value}")
    print(f"  (not reported: {', '.join(sorted(missing))})")


MODE_SPELLING = {
    MODE_AUTO: "Auto",
    MODE_AI: "AI",
    MODE_MANUAL: "Manual",
    MODE_PASSIVE: "Passive",
    MODE_UPS: "UPS",
}


async def _inspect_one(client: MarstekUDPClient, ip: str) -> None:
    print(f"\n### Device {ip} ###")
    try:
        info = await client.get_device_info(ip)
    except (TimeoutError, OSError, TypeError) as err:
        print(f"  get_device_info failed: {err}")
        return

    print(f"  type        = {info.device_type}")
    print(f"  version     = {info.version}")
    print(f"  wifi_name   = {info.wifi_name or '(none)'}")
    print(f"  wifi_mac    = {info.wifi_mac or '(none)'}")
    print(f"  ble_mac     = {info.ble_mac or '(none)'}")
    print(f"  stable_id   = {info.stable_id or '(none)'}")

    # The first poll does not refresh extras (WiFi/BLE/EM); run a few polls so
    # the throttled components are queried at least once.
    status = await client.get_device_status(ip, delay_ms=0)
    _dump_status(status, "poll 1 (essentials)")

    for _ in range(2, 7):
        await client.get_device_status(ip, delay_ms=0)

    final = await client.get_device_status(ip, delay_ms=0)
    _dump_status(final, "poll 7 (with extras)")

    raw = client.get_last_results(ip)
    print("\n---------- raw replies ----------")
    for method, result in sorted(raw.items()):
        print(f"  {method:16} = {json.dumps(result, ensure_ascii=False)}")


async def _discover(client: MarstekUDPClient) -> list[str]:
    print("Broadcasting for Marstek devices (10s)...")
    devices = await client.discover_devices(use_cache=False)
    if not devices:
        print("  no devices responded")
        return []
    for device in devices:
        print(f"  found {device.device_type} v{device.version} @ {device.ip}")
    return [d.ip for d in devices if d.ip]


async def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    client = MarstekUDPClient()
    await client.async_setup()
    try:
        if argv[1] == "discover":
            ips = await _discover(client)
            for ip in ips:
                await _inspect_one(client, ip)
        else:
            await _inspect_one(client, argv[1])
    finally:
        await client.async_cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
