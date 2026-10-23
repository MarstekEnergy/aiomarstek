# aiomarstek

[![PyPI](https://img.shields.io/pypi/v/aiomarstek.svg)](https://pypi.org/project/aiomarstek/)
[![Python](https://img.shields.io/pypi/pyversions/aiomarstek.svg)](https://pypi.org/project/aiomarstek/)
[![CI](https://github.com/MarstekEnergy/aiomarstek/actions/workflows/ci.yml/badge.svg)](https://github.com/MarstekEnergy/aiomarstek/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/MarstekEnergy/aiomarstek/blob/main/LICENSE)

Async client library for local Marstek device communication.

`aiomarstek` contains the UDP protocol, command builder, discovery cache,
polling pause handling, and high-level status helpers used by the Home Assistant
Marstek integration. `hold_glitches` runs inside `get_device_status`, so every
consumer gets the same protection: a 0 % SOC reading right after a high level,
and a single dip of a lifetime energy counter, are held at their previous value
until they persist over a few polls (then accepted as real). Counters that do
persist are passed on, and the sensors expose them as `TOTAL_INCREASING`, for
which Home Assistant treats a lower reading as a meter reset.

## Installation

```bash
python -m pip install aiomarstek
```

## Example

```python
import asyncio

from aiomarstek import MarstekUDPClient


async def main() -> None:
    client = MarstekUDPClient()
    await client.async_setup()
    try:
        devices = await client.discover_devices()
        print(devices)
    finally:
        await client.async_cleanup()


asyncio.run(main())
```

## Security considerations

The protocol is unauthenticated: `build_command` emits only `id`, `method` and
`params`, and responses carry no token or signature either. The trust boundary
is therefore the network — any host that can reach a device's UDP port can send
it any command, and any host can answer a broadcast discovery request.

What the library does mitigate: with `verify_source_ip=True` (the default) a
unicast response whose source address does not match the device the request was
sent to is dropped, so another host cannot forge a status reply or fake a write
success. That says nothing about whether the device itself validates the
commands it receives — assume it does not until Marstek documents otherwise.

- Never expose the device UDP port (default `30000`) to untrusted networks or
  the internet; keep devices on a segment that only trusted hosts can reach.
- The client binds `0.0.0.0:<local_port>` (default `30000`) for replies;
  restrict inbound UDP on that port to the device addresses.
- Discovery accepts responses from any host, so treat discovery results as
  untrusted input.
- Debug logging records full request/response JSON and peer addresses; disable
  it after troubleshooting and scrub network identifiers before sharing logs.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
python -m ruff check .
python -m build
```
