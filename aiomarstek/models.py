"""Models for Marstek devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

MarstekDeviceVersion: TypeAlias = int | str


@dataclass(frozen=True, slots=True)
class MarstekDeviceInfo:
    """Normalized device information returned by a Marstek device."""

    id: object | None
    device_type: str
    version: MarstekDeviceVersion
    wifi_name: str
    ip: str
    wifi_mac: str
    ble_mac: str
    mac: str

    @classmethod
    def from_response(
        cls, device_info: Mapping[str, object], host: str | None = None
    ) -> MarstekDeviceInfo:
        """Parse a device discovery or information response."""
        wifi_mac = _string_value(device_info.get("wifi_mac"))
        ble_mac = _string_value(device_info.get("ble_mac"))

        return cls(
            id=device_info.get("id"),
            device_type=_string_value(
                device_info.get("device_type", device_info.get("device")), "Unknown"
            ),
            version=_version_value(device_info.get("version", device_info.get("ver"))),
            wifi_name=_string_value(device_info.get("wifi_name")),
            ip=_string_value(device_info.get("ip") or host),
            wifi_mac=wifi_mac,
            ble_mac=ble_mac,
            mac=_string_value(device_info.get("mac")) or wifi_mac or ble_mac,
        )

    @property
    def stable_id(self) -> str:
        """Return the stable hardware identifier for the device."""
        return self.mac or self.wifi_mac or self.ble_mac


def _string_value(value: object, default: str = "") -> str:
    """Return value as a string, or a default for missing values."""
    if value is None:
        return default
    value = str(value)
    return value or default


def _version_value(value: object) -> MarstekDeviceVersion:
    """Return a display-safe firmware version value."""
    return value if isinstance(value, int | str) and value != "" else 0
