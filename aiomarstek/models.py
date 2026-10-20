"""Models for Marstek devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, TypeAlias

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

# A battery level this high cannot drop to 0 % within one poll.
SOC_GLITCH_FLOOR: Final = 10
# Polls in a row reporting 0 % after which the reading is accepted as real,
# so a device that is really empty is not held at its last level forever.
ZERO_SOC_POLLS: Final = 3
# Lifetime counters that only ever grow on a healthy device; the Open API
# documents them as cumulative totals that only a factory reset clears.
MONOTONIC_KEYS: Final = (
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
)
# Polls in a row a counter has to stay below its previous reading before the
# drop is accepted as a real reset: a single dip is a device glitch, which
# would otherwise show up as a phantom meter reset in accumulating stats.
FALLING_ENERGY_POLLS: Final = 2


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
            mac=_string_value(device_info.get("mac")) or ble_mac,
        )

    @property
    def stable_id(self) -> str:
        """Return the stable hardware identifier for the device."""
        return self.mac or self.ble_mac


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
    total_pv_energy: MarstekStatusNumber | None = None
    total_grid_output_energy: MarstekStatusNumber | None = None
    total_grid_input_energy: MarstekStatusNumber | None = None
    total_load_energy: MarstekStatusNumber | None = None

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

    def with_es_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with an ES.GetStatus response."""
        battery_soc = _response_number(response, "bat_soc", self.battery_soc)
        ongrid_power = _response_number(response, "ongrid_power", self.battery_power)
        # The Open API reports PV energy in 0.01 kWh and the other energy
        # counters in Wh. They are lifetime totals and never reset, so a
        # lower reading is a device glitch (see hold_glitches).
        total_pv_energy = _response_scaled_number(
            response, "total_pv_energy", self.total_pv_energy, 10
        )
        total_grid_output_energy = _response_number(
            response, "total_grid_output_energy", self.total_grid_output_energy
        )
        total_grid_input_energy = _response_number(
            response, "total_grid_input_energy", self.total_grid_input_energy
        )
        total_load_energy = _response_number(
            response, "total_load_energy", self.total_load_energy
        )
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
            battery_status=battery_status,
            total_pv_energy=total_pv_energy,
            total_grid_output_energy=total_grid_output_energy,
            total_grid_input_energy=total_grid_input_energy,
            total_load_energy=total_load_energy,
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


def hold_glitches(
    previous: MarstekDeviceStatus | None,
    current: MarstekDeviceStatus,
    zero_soc_polls: int = 0,
    falling_energy_polls: int = 0,
) -> MarstekDeviceStatus:
    """Keep previous values where the firmware reports implausible readings.

    The firmware occasionally reports a battery level of 0 % right after a
    reading of 10 % or more, and a lifetime counter below its previous
    reading. Such readings are held at their previous value, so a single bad
    reply shows up neither as a spike nor as a phantom meter reset. Both are
    accepted as real once they repeat over several polls; the caller counts
    those polls and passes the counts as ``zero_soc_polls`` and
    ``falling_energy_polls``.
    """
    if previous is None:
        return current
    held: dict[str, MarstekStatusValue | None] = {}
    for key in MONOTONIC_KEYS:
        old, new = getattr(previous, key), getattr(current, key)
        if (
            old is not None
            and (new is None or new < old)
            and falling_energy_polls < FALLING_ENERGY_POLLS
        ):
            held[key] = old
    if (
        current.battery_soc == 0
        and (previous.battery_soc or 0) >= SOC_GLITCH_FLOOR
        and zero_soc_polls < ZERO_SOC_POLLS
    ):
        held["battery_soc"] = previous.battery_soc
    if held:
        return replace(current, **held)
    return current


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


def _response_scaled_number(
    response: Mapping[str, object],
    key: str,
    previous_value: MarstekStatusNumber | None,
    scale: int | float,
) -> MarstekStatusNumber | None:
    """Return a numeric response field converted to its normalized unit."""
    if key not in response:
        return previous_value
    value = _response_number(response, key, None)
    return value * scale if value is not None else None


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
