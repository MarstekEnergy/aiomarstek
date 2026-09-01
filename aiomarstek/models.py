"""Models for Marstek devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypeAlias

MarstekDeviceVersion: TypeAlias = int | str
MarstekStatusNumber: TypeAlias = int | float
MarstekStatusValue: TypeAlias = MarstekStatusNumber | str

_DEVICE_MODE_MAP = {
    0: "auto",
    1: "ai",
    2: "manual",
    3: "passive",
    4: "ups",
}
_PV_STATE_MAP = {0: "standby", 1: "working"}


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


@dataclass(frozen=True, slots=True)
class MarstekDeviceStatus:
    """Normalized status data returned by a Marstek device."""

    device_ip: str
    battery_soc: MarstekStatusNumber | None = None
    battery_power: MarstekStatusNumber | None = None
    device_mode: str | None = None
    battery_status: str | None = None
    pv1_power: MarstekStatusNumber | None = None
    pv1_voltage: MarstekStatusNumber | None = None
    pv1_current: MarstekStatusNumber | None = None
    pv1_state: str | None = None
    pv2_power: MarstekStatusNumber | None = None
    pv2_voltage: MarstekStatusNumber | None = None
    pv2_current: MarstekStatusNumber | None = None
    pv2_state: str | None = None
    pv3_power: MarstekStatusNumber | None = None
    pv3_voltage: MarstekStatusNumber | None = None
    pv3_current: MarstekStatusNumber | None = None
    pv3_state: str | None = None
    pv4_power: MarstekStatusNumber | None = None
    pv4_voltage: MarstekStatusNumber | None = None
    pv4_current: MarstekStatusNumber | None = None
    pv4_state: str | None = None

    def with_es_mode_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with an ES.GetMode response."""
        battery_soc = _response_number(response, "bat_soc", self.battery_soc)
        ongrid_power = _response_number(response, "ongrid_power", self.battery_power)
        device_mode = _response_mode(response, "mode", self.device_mode)

        battery_status = self.battery_status
        if "ongrid_power" in response:
            if ongrid_power is None:
                battery_status = None
            elif ongrid_power > 0:
                battery_status = "selling"
            elif ongrid_power < 0:
                battery_status = "charging"
            else:
                battery_status = "idle"

        return replace(
            self,
            battery_soc=battery_soc,
            battery_power=abs(ongrid_power) if ongrid_power is not None else None,
            device_mode=device_mode,
            battery_status=battery_status,
        )

    def with_pv_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with a PV.GetStatus response."""
        values: dict[str, MarstekStatusValue | None] = {}
        for channel in range(1, 5):
            for metric in ("power", "voltage", "current"):
                key = f"pv{channel}_{metric}"
                values[key] = _response_number(response, key, getattr(self, key))
            key = f"pv{channel}_state"
            values[key] = _response_pv_state(response, key, getattr(self, key))

        return replace(self, **values)

    def has_value(self, key: str) -> bool:
        """Return whether a status field has a value."""
        return self.get_value(key) is not None

    def get_value(self, key: str) -> MarstekStatusValue | None:
        """Return a normalized status value."""
        return getattr(self, key)


def _string_value(value: object, default: str = "") -> str:
    """Return value as a string, or a default for missing values."""
    if value is None:
        return default
    value = str(value)
    return value or default


def _version_value(value: object) -> MarstekDeviceVersion:
    """Return a display-safe firmware version value."""
    return value if isinstance(value, int | str) and value != "" else 0


def _response_number(
    response: Mapping[str, object], key: str, previous_value: MarstekStatusNumber | None
) -> MarstekStatusNumber | None:
    """Return a numeric response field or its previous value when absent."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number or null")
    return value


def _response_mode(
    response: Mapping[str, object], key: str, previous_value: str | None
) -> str | None:
    """Return a normalized operating mode response field."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.casefold()
        if normalized in _DEVICE_MODE_MAP.values():
            return normalized
    elif isinstance(value, int) and not isinstance(value, bool):
        if normalized := _DEVICE_MODE_MAP.get(value):
            return normalized
    raise TypeError(f"{key} must be a supported operating mode or null")


def _response_pv_state(
    response: Mapping[str, object], key: str, previous_value: str | None
) -> str | None:
    """Return a normalized PV state response field."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (normalized := _PV_STATE_MAP.get(value))
    ):
        return normalized
    raise TypeError(f"{key} must be 0, 1, or null")
