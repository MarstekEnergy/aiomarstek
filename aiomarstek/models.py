"""Models for Marstek devices."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, TypeAlias

MarstekDeviceVersion: TypeAlias = int | str
MarstekStatusNumber: TypeAlias = int | float
MarstekStatusValue: TypeAlias = MarstekStatusNumber | str

MODE_AUTO: Final = "auto"
MODE_AI: Final = "ai"
MODE_MANUAL: Final = "manual"
MODE_PASSIVE: Final = "passive"
MODE_UPS: Final = "ups"

# Normalized mode names (library-facing) mapped to the spellings the Open API
# expects in an ES.SetMode "mode" field.
_DEVICE_MODE_MAP = {
    0: "auto",
    1: "ai",
    2: "manual",
    3: "passive",
    4: "ups",
}
_MODE_API_SPELLING = {
    "auto": "Auto",
    "ai": "AI",
    "manual": "Manual",
    "passive": "Passive",
    "ups": "UPS",
}
_PV_STATE_MAP = {0: "standby", 1: "working"}

# Manual mode supports ten time slots on Venus A/C/D/E and six on Venus E mini.
MANUAL_SLOT_MAX: Final = 9
MANUAL_SLOT_MINI_MAX: Final = 5

# manual_set values documented for Venus E mini time slots.
MANUAL_SET_DISABLE: Final = 0
MANUAL_SET_CHARGE: Final = 1
MANUAL_SET_DISCHARGE: Final = 2
MANUAL_SET_AUTO: Final = 3

# A battery level this high cannot drop to 0 % within one poll.
SOC_GLITCH_FLOOR: Final = 10
# Polls in a row reporting 0 % after which the reading is accepted as real,
# so a device that is really empty is not held at its last level forever.
ZERO_SOC_POLLS: Final = 3
# Lifetime counters that only ever grow on a healthy device; the Open API
# documents them as cumulative totals that only a factory reset clears.
# total_pv_energy is reported in 0.01 kWh and the rest in Wh (input/output
# energy in 0.1 Wh), but all are monotonic, so a drop is a firmware glitch.
MONOTONIC_KEYS: Final = (
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
    "total_load_energy",
    "ct_input_energy",
    "ct_output_energy",
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
class MarstekManualSlot:
    """A single Manual-mode time slot for the ES.SetMode command."""

    time_num: int
    start_time: str
    end_time: str
    week_set: int
    power: int
    enable: bool = True
    manual_set: int | None = None

    def __post_init__(self) -> None:
        """Validate slot fields within their documented ranges."""
        if not 0 <= self.time_num <= MANUAL_SLOT_MAX:
            raise ValueError(
                f"time_num must be 0..{MANUAL_SLOT_MAX}, got {self.time_num}"
            )
        if self.week_set < 0:
            raise ValueError(f"week_set must be non-negative, got {self.week_set}")
        if not self.start_time or not self.end_time:
            raise ValueError("start_time and end_time must be non-empty")
        if self.manual_set is not None and self.manual_set not in (
            MANUAL_SET_DISABLE,
            MANUAL_SET_CHARGE,
            MANUAL_SET_DISCHARGE,
            MANUAL_SET_AUTO,
        ):
            raise ValueError(f"manual_set must be one of 0..3, got {self.manual_set}")

    def to_payload(self) -> dict[str, object]:
        """Return the manual_cfg entry for this slot."""
        payload: dict[str, object] = {
            "time_num": self.time_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "week_set": self.week_set,
            "power": self.power,
            "enable": 1 if self.enable else 0,
        }
        if self.manual_set is not None:
            payload["manual_set"] = self.manual_set
        return payload


@dataclass(frozen=True, slots=True)
class MarstekPassiveConfig:
    """Passive mode parameters for the ES.SetMode command."""

    power: int
    cd_time: int


@dataclass(frozen=True, slots=True)
class MarstekModeConfig:
    """Operating mode configuration for the ES.SetMode command."""

    mode: str
    manual_slots: tuple[MarstekManualSlot, ...] = ()
    passive: MarstekPassiveConfig | None = None
    ups_enabled: bool = True

    def __post_init__(self) -> None:
        """Validate the mode and its required sub-configuration."""
        if self.mode not in _MODE_API_SPELLING:
            raise ValueError(
                f"mode must be one of {', '.join(_MODE_API_SPELLING)}, "
                f"got {self.mode!r}"
            )
        if self.mode == MODE_MANUAL and not self.manual_slots:
            raise ValueError("manual mode requires at least one manual slot")
        if self.mode == MODE_PASSIVE and self.passive is None:
            raise ValueError("passive mode requires a passive config")

    def to_payload(self) -> dict[str, object]:
        """Return the ES.SetMode "config" object."""
        mode = _MODE_API_SPELLING.get(self.mode, self.mode)
        config: dict[str, object] = {"mode": mode}
        if self.mode == MODE_AUTO:
            config["auto_cfg"] = {"enable": 1}
        elif self.mode == MODE_AI:
            config["ai_cfg"] = {"enable": 1}
        elif self.mode == MODE_MANUAL:
            config["manual_cfg"] = [slot.to_payload() for slot in self.manual_slots]
        elif self.mode == MODE_PASSIVE and self.passive is not None:
            config["passive_cfg"] = {
                "power": self.passive.power,
                "cd_time": self.passive.cd_time,
            }
        elif self.mode == MODE_UPS:
            config["ups_cfg"] = {"enable": 1 if self.ups_enabled else 0}
        return config


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
    # ES.GetStatus additional fields
    battery_capacity: MarstekStatusNumber | None = None
    pv_power: MarstekStatusNumber | None = None
    ongrid_power: MarstekStatusNumber | None = None
    offgrid_power: MarstekStatusNumber | None = None
    # ES.GetMode / EM.GetStatus CT fields
    ct_state: bool | None = None
    a_power: MarstekStatusNumber | None = None
    b_power: MarstekStatusNumber | None = None
    c_power: MarstekStatusNumber | None = None
    total_power: MarstekStatusNumber | None = None
    ct_input_energy: MarstekStatusNumber | None = None
    ct_output_energy: MarstekStatusNumber | None = None
    # Bat.GetStatus fields
    battery_temperature: MarstekStatusNumber | None = None
    battery_remaining_capacity: MarstekStatusNumber | None = None
    battery_rated_capacity: MarstekStatusNumber | None = None
    charge_permission: bool | None = None
    discharge_permission: bool | None = None
    # Wifi.GetStatus fields
    wifi_ssid: str | None = None
    wifi_rssi: MarstekStatusNumber | None = None
    wifi_ip: str | None = None
    wifi_gateway: str | None = None
    wifi_netmask: str | None = None
    wifi_dns: str | None = None
    # BLE.GetStatus fields
    ble_state: str | None = None

    def with_es_mode_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with an ES.GetMode response."""
        battery_soc = _response_number(response, "bat_soc", self.battery_soc)
        ongrid_power = _response_number(response, "ongrid_power", self.ongrid_power)
        offgrid_power = _response_number(response, "offgrid_power", self.offgrid_power)
        device_mode = _response_mode(response, "mode", self.device_mode)
        ct_state = _response_ct_state(response, "ct_state", self.ct_state)
        a_power = _response_number(response, "a_power", self.a_power)
        b_power = _response_number(response, "b_power", self.b_power)
        c_power = _response_number(response, "c_power", self.c_power)
        total_power = _response_number(response, "total_power", self.total_power)
        ct_input_energy = _response_scaled_number(
            response, "input_energy", self.ct_input_energy, 0.1
        )
        ct_output_energy = _response_scaled_number(
            response, "output_energy", self.ct_output_energy, 0.1
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

        battery_power = self.battery_power
        if ongrid_power is not None:
            battery_power = abs(ongrid_power)

        return replace(
            self,
            battery_soc=battery_soc,
            battery_power=battery_power,
            device_mode=device_mode,
            battery_status=battery_status,
            ongrid_power=ongrid_power,
            offgrid_power=offgrid_power,
            ct_state=ct_state,
            a_power=a_power,
            b_power=b_power,
            c_power=c_power,
            total_power=total_power,
            ct_input_energy=ct_input_energy,
            ct_output_energy=ct_output_energy,
        )

    def with_es_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with an ES.GetStatus response."""
        battery_soc = _response_number(response, "bat_soc", self.battery_soc)
        ongrid_power = _response_number(response, "ongrid_power", self.ongrid_power)
        offgrid_power = _response_number(response, "offgrid_power", self.offgrid_power)
        bat_power = _response_number(response, "bat_power", None)
        battery_capacity = _response_number(response, "bat_cap", self.battery_capacity)
        pv_power = _response_number(response, "pv_power", self.pv_power)
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

        # Prefer the dedicated battery power reading; the older devices only
        # report the grid-tied power, from which battery power is derived.
        # The reported magnitude is direction-agnostic (see battery_status).
        if bat_power is not None:
            battery_power = abs(bat_power)
        elif ongrid_power is not None:
            battery_power = abs(ongrid_power)
        else:
            battery_power = self.battery_power

        return replace(
            self,
            battery_soc=battery_soc,
            battery_power=battery_power,
            battery_status=battery_status,
            ongrid_power=ongrid_power,
            offgrid_power=offgrid_power,
            battery_capacity=battery_capacity,
            pv_power=pv_power,
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

    def with_battery_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with a Bat.GetStatus response."""
        battery_soc = _response_number(response, "soc", self.battery_soc)
        battery_temperature = _response_number(
            response, "bat_temp", self.battery_temperature
        )
        battery_remaining_capacity = _response_number(
            response, "bat_capacity", self.battery_remaining_capacity
        )
        battery_rated_capacity = _response_number(
            response, "rated_capacity", self.battery_rated_capacity
        )
        charge_permission = _response_bool(
            response, "charg_flag", self.charge_permission
        )
        discharge_permission = _response_bool(
            response, "dischrg_flag", self.discharge_permission
        )

        return replace(
            self,
            battery_soc=battery_soc,
            battery_temperature=battery_temperature,
            battery_remaining_capacity=battery_remaining_capacity,
            battery_rated_capacity=battery_rated_capacity,
            charge_permission=charge_permission,
            discharge_permission=discharge_permission,
        )

    def with_wifi_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with a Wifi.GetStatus response."""
        return replace(
            self,
            wifi_ssid=_response_str(response, "ssid", self.wifi_ssid),
            wifi_rssi=_response_number(response, "rssi", self.wifi_rssi),
            wifi_ip=_response_str(response, "sta_ip", self.wifi_ip),
            wifi_gateway=_response_str(response, "sta_gate", self.wifi_gateway),
            wifi_netmask=_response_str(response, "sta_mask", self.wifi_netmask),
            wifi_dns=_response_str(response, "sta_dns", self.wifi_dns),
        )

    def with_ble_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with a BLE.GetStatus response."""
        return replace(
            self,
            ble_state=_response_str(response, "state", self.ble_state),
        )

    def with_em_status_response(
        self, response: Mapping[str, object]
    ) -> MarstekDeviceStatus:
        """Return status updated with an EM.GetStatus response.

        The energy meter reports the same CT fields that ES.GetMode returns
        in Auto and AI mode; values from either source update the same
        normalized fields.
        """
        ct_state = _response_ct_state(response, "ct_state", self.ct_state)
        a_power = _response_number(response, "a_power", self.a_power)
        b_power = _response_number(response, "b_power", self.b_power)
        c_power = _response_number(response, "c_power", self.c_power)
        total_power = _response_number(response, "total_power", self.total_power)
        ct_input_energy = _response_scaled_number(
            response, "input_energy", self.ct_input_energy, 0.1
        )
        ct_output_energy = _response_scaled_number(
            response, "output_energy", self.ct_output_energy, 0.1
        )

        return replace(
            self,
            ct_state=ct_state,
            a_power=a_power,
            b_power=b_power,
            c_power=c_power,
            total_power=total_power,
            ct_input_energy=ct_input_energy,
            ct_output_energy=ct_output_energy,
        )

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
    falling_energy_polls: int | Mapping[str, int] = 0,
) -> MarstekDeviceStatus:
    """Keep previous values where the firmware reports implausible readings.

    The firmware occasionally reports a battery level of 0 % right after a
    reading of 10 % or more, and a lifetime counter below its previous
    reading. Such readings are held at their previous value, so a single bad
    reply shows up neither as a spike nor as a phantom meter reset. Both are
    accepted as real once they repeat over several polls; the caller counts
    those polls and passes the counts as ``zero_soc_polls`` and
    ``falling_energy_polls``. Counter polls are tracked per counter, so one
    counter resetting does not let another's drop through. A missing value
    (``None``) is held at the last reading but does not count as a drop.
    """
    if previous is None:
        return current
    per_key = (
        falling_energy_polls if isinstance(falling_energy_polls, Mapping) else None
    )
    held: dict[str, MarstekStatusValue | None] = {}
    for key in MONOTONIC_KEYS:
        old, new = getattr(previous, key), getattr(current, key)
        if old is None:
            continue
        if new is None:
            held[key] = old
            continue
        if new < old:
            polls = per_key.get(key, 0) if per_key is not None else falling_energy_polls
            if polls < FALLING_ENERGY_POLLS:
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
    """Return a numeric response field or its previous value when absent.

    Non-finite floats (NaN, Infinity) are rejected: Python's JSON parser
    accepts them by default, but they cannot represent a real sensor
    reading and would also bypass the monotonic-counter glitch checks.
    """
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number or null")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number, got {value!r}")
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


def _response_bool(
    response: Mapping[str, object], key: str, previous_value: bool | None
) -> bool | None:
    """Return a boolean response field or its previous value when absent."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise TypeError(f"{key} must be a boolean, 0, 1, or null")


def _response_ct_state(
    response: Mapping[str, object], key: str, previous_value: bool | None
) -> bool | None:
    """Return a CT connection state field (0: off, 1: connected)."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    raise TypeError(f"{key} must be 0, 1, or null")


def _response_str(
    response: Mapping[str, object], key: str, previous_value: str | None
) -> str | None:
    """Return a string response field or its previous value when absent."""
    if key not in response:
        return previous_value

    value = response[key]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TypeError(f"{key} must be a string or null")


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
