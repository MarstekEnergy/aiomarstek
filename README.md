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

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
python -m ruff check .
python -m build
```
