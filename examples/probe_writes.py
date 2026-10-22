"""Configuration command probe (dry-run by default).

Confirms that the write path talks to the device, without changing anything
unless you opt in. Start safe with the lowest-impact command (LED toggle),
then move to DOD only once read-back is confirmed.

    # Dry-run: show what *would* be sent, do not transmit.
    .venv/bin/python examples/probe_writes.py 192.168.1.10 --command led --on

    # Actually send (the device will change state; LED is reversible).
    .venv/bin/python examples/probe_writes.py 192.168.1.10 --command led --on --apply

Supported commands: led | dod | ble | power_version | mode

This script is intentionally side-effect-heavy when --apply is given. Read it
before running, and do not use --command factory_reset casually.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aiomarstek import (
    DOD_MAX,
    DOD_MIN,
    POWER_VERSIONS,
    MarstekModeConfig,
    MarstekSetError,
    MarstekUDPClient,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip")
    parser.add_argument(
        "--command",
        choices=["led", "dod", "ble", "power_version", "mode"],
        required=True,
    )
    parser.add_argument("--on", action="store_true", help="led: turn on")
    parser.add_argument("--off", action="store_true", help="led: turn off")
    parser.add_argument("--value", type=int, help="dod / power_version value")
    parser.add_argument("--enable-ble", action="store_true", help="ble: advertising on")
    parser.add_argument(
        "--disable-ble", action="store_true", help="ble: advertising off"
    )
    parser.add_argument("--mode", help="mode: auto/ai/manual/passive/ups")
    parser.add_argument(
        "--apply", action="store_true", help="actually transmit the command"
    )
    return parser


async def _probe(client: MarstekUDPClient, args: argparse.Namespace) -> None:
    print(f"\n=== {args.command} @ {args.ip}  (apply={args.apply}) ===")

    if args.command == "led":
        on = args.on and not args.off
        print(f"  set_led(on={on})")
        if args.apply:
            await client.set_led(args.ip, on)
        return

    if args.command == "dod":
        value = args.value
        if value is None or not DOD_MIN <= value <= DOD_MAX:
            raise SystemExit(f"--value must be {DOD_MIN}..{DOD_MAX}")
        print(f"  set_dod(value={value})")
        if args.apply:
            await client.set_dod(args.ip, value)
        return

    if args.command == "ble":
        if args.enable_ble == args.disable_ble:
            raise SystemExit("pass exactly one of --enable-ble / --disable-ble")
        enabled = args.enable_ble
        print(f"  set_bluetooth_advertising(enabled={enabled})")
        if args.apply:
            await client.set_bluetooth_advertising(args.ip, enabled)
        return

    if args.command == "power_version":
        value = args.value
        if value not in POWER_VERSIONS:
            raise SystemExit(f"--value must be one of {POWER_VERSIONS}")
        print(f"  set_power_version(version={value})")
        if args.apply:
            await client.set_power_version(args.ip, value)
        return

    if args.command == "mode":
        mode = (args.mode or "").lower()
        config = MarstekModeConfig(mode=mode)
        print(f"  set_device_mode(mode={mode})  payload={config.to_payload()}")
        if args.apply:
            await client.set_device_mode(args.ip, config)
        return


async def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv[1:])
    client = MarstekUDPClient()
    await client.async_setup()
    try:
        await _probe(client, args)
    except MarstekSetError as err:
        print(f"  FAILED: {err}", file=sys.stderr)
        return 1
    finally:
        await client.async_cleanup()
    if not args.apply:
        print("  (dry-run only — re-run with --apply to transmit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
