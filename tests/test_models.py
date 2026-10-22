"""Tests for Marstek device models."""

import pytest

from aiomarstek import (
    FALLING_ENERGY_POLLS,
    MANUAL_SET_AUTO,
    MANUAL_SLOT_MAX,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    ZERO_SOC_POLLS,
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekManualSlot,
    MarstekModeConfig,
    hold_glitches,
)


def test_device_info_parses_discovery_response() -> None:
    """Test parsing a discovery response into a device model."""
    device = MarstekDeviceInfo.from_response(
        {
            "id": 1,
            "device": "VenusE 3.0",
            "ver": 2,
            "wifi_name": "home",
            "ip": "192.168.1.10",
            "wifi_mac": "AA:BB:CC:DD:EE:FF",
            "ble_mac": "11:22:33:44:55:66",
        }
    )

    assert device == MarstekDeviceInfo(
        id=1,
        device_type="VenusE 3.0",
        version=2,
        wifi_name="home",
        ip="192.168.1.10",
        wifi_mac="AA:BB:CC:DD:EE:FF",
        ble_mac="11:22:33:44:55:66",
        mac="11:22:33:44:55:66",
    )
    assert device.stable_id == "11:22:33:44:55:66"


def test_device_info_uses_host_when_response_has_no_ip() -> None:
    """Test parsing uses the requested host when no IP is returned."""
    device = MarstekDeviceInfo.from_response({"device": "VenusE 3.0"}, "192.168.1.10")

    assert device.ip == "192.168.1.10"


def test_device_info_uses_ble_mac_as_stable_id_without_wifi_mac() -> None:
    """ble_mac is the stable ID even when wifi_mac is absent."""
    device = MarstekDeviceInfo.from_response(
        {"device": "VenusE 3.0", "ble_mac": "11:22:33:44:55:66"}
    )

    assert device.mac == "11:22:33:44:55:66"
    assert device.stable_id == "11:22:33:44:55:66"


def test_device_info_prefers_explicit_mac_field() -> None:
    """An explicit mac field takes precedence over ble_mac."""
    device = MarstekDeviceInfo.from_response(
        {
            "device": "VenusE 3.0",
            "mac": "AA:BB:CC:DD:EE:FF",
            "ble_mac": "11:22:33:44:55:66",
        }
    )

    assert device.mac == "AA:BB:CC:DD:EE:FF"
    assert device.stable_id == "AA:BB:CC:DD:EE:FF"


def test_device_info_falls_back_to_ble_mac_when_mac_empty() -> None:
    """An empty mac field falls back to ble_mac."""
    device = MarstekDeviceInfo.from_response(
        {"device": "VenusE 3.0", "mac": "", "ble_mac": "11:22:33:44:55:66"}
    )

    assert device.mac == "11:22:33:44:55:66"
    assert device.stable_id == "11:22:33:44:55:66"


def test_devices_with_shared_wifi_mac_keep_distinct_stable_ids() -> None:
    """Two devices sharing wifi_mac stay distinct via their ble_mac."""
    first = MarstekDeviceInfo.from_response(
        {
            "device": "VenusE 3.0",
            "wifi_mac": "AA:BB:CC:DD:EE:FF",
            "ble_mac": "11:22:33:44:55:66",
        }
    )
    second = MarstekDeviceInfo.from_response(
        {
            "device": "VenusE 3.0",
            "wifi_mac": "AA:BB:CC:DD:EE:FF",
            "ble_mac": "66:55:44:33:22:11",
        }
    )

    assert first.stable_id == "11:22:33:44:55:66"
    assert second.stable_id == "66:55:44:33:22:11"
    assert first.stable_id != second.stable_id


def test_device_status_parses_protocol_values() -> None:
    """Test status parsing follows the Open API field types and values."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10")

    status = status.with_es_mode_response(
        {"bat_soc": 98, "ongrid_power": -100.5, "mode": "Auto"}
    )
    status = status.with_es_status_response({"total_pv_energy": 281})
    status = status.with_pv_status_response(
        {
            "pv1_power": 42.5,
            "pv1_voltage": 10.5,
            "pv1_current": 4.25,
            "pv1_state": 1,
        }
    )

    assert status.battery_soc == 98
    assert status.battery_power == 100.5
    assert status.device_mode == "auto"
    assert status.battery_status == "charging"
    assert status.total_pv_energy == 2810
    assert status.pv1_power == 42.5
    assert status.pv1_voltage == 10.5
    assert status.pv1_current == 4.25
    assert status.pv1_state == "working"
    assert status.has_value("pv2_power") is False

    assert status.with_es_status_response({}).total_pv_energy == 2810


def test_device_status_parses_energy_totals() -> None:
    """Grid and load energy counters are reported in Wh, unlike PV energy."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10").with_es_status_response(
        {
            "total_grid_output_energy": 2328,
            "total_grid_input_energy": 456,
            "total_load_energy": 46,
        }
    )

    assert status.total_grid_output_energy == 2328
    assert status.total_grid_input_energy == 456
    assert status.total_load_energy == 46
    assert status.with_es_status_response({}).total_grid_input_energy == 456


def test_hold_glitches_keeps_single_counter_dip() -> None:
    """A counter below its previous reading is held for a single poll."""
    previous = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=1000,
        total_grid_output_energy=2000,
        total_grid_input_energy=3000,
    )
    current = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=0,
        total_grid_output_energy=5,
        total_grid_input_energy=None,
    )

    held = hold_glitches(previous, current)
    assert held.total_pv_energy == 1000
    assert held.total_grid_output_energy == 2000
    assert held.total_grid_input_energy == 3000


def test_hold_glitches_accepts_counter_reset_after_repeats() -> None:
    """A counter drop is accepted once it persists over several polls."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=1000)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=0)

    assert (
        hold_glitches(
            previous, current, falling_energy_polls=FALLING_ENERGY_POLLS
        ).total_pv_energy
        == 0
    )


def test_hold_glitches_passes_through_rising_counters() -> None:
    """Counters at or above their previous reading are returned as is."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=1000)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=1005)

    assert hold_glitches(previous, current) is current


def test_hold_glitches_keeps_soc_spike_to_zero() -> None:
    """A 0 % reading straight after a high level is held."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=50)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=0)

    assert hold_glitches(previous, current).battery_soc == 50


def test_hold_glitches_accepts_zero_soc_after_repeats() -> None:
    """0 % is accepted once it repeats over several polls."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=50)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=0)

    assert hold_glitches(previous, current, ZERO_SOC_POLLS).battery_soc == 0


def test_hold_glitches_accepts_zero_soc_from_low_level() -> None:
    """A drop to 0 % from below the glitch floor is taken as is."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=5)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=0)

    assert hold_glitches(previous, current).battery_soc == 0


def test_hold_glitches_without_previous_returns_current() -> None:
    """The first reading is returned unchanged."""
    current = MarstekDeviceStatus(device_ip="192.168.1.10", battery_soc=0)

    assert hold_glitches(None, current) is current


def test_hold_glitches_tracks_counters_separately() -> None:
    """One counter resetting does not let another's drop through."""
    previous = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=1000,
        total_grid_output_energy=2000,
    )
    current = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=1000,
        total_grid_output_energy=5,
    )
    held = hold_glitches(
        previous, current, falling_energy_polls={"total_grid_output_energy": 0}
    )
    assert held.total_pv_energy == 1000
    assert held.total_grid_output_energy == 2000


def test_hold_glitches_accepts_one_reset_but_holds_another() -> None:
    """A counter past the escape threshold passes while another is still held."""
    previous = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=1000,
        total_grid_output_energy=2000,
    )
    current = MarstekDeviceStatus(
        device_ip="192.168.1.10",
        total_pv_energy=0,
        total_grid_output_energy=5,
    )
    held = hold_glitches(
        previous,
        current,
        falling_energy_polls={
            "total_pv_energy": FALLING_ENERGY_POLLS,
            "total_grid_output_energy": 0,
        },
    )
    assert held.total_pv_energy == 0
    assert held.total_grid_output_energy == 2000


def test_hold_glitches_holds_missing_counter_without_counting_it() -> None:
    """A missing value is held at the last reading and never escapes."""
    previous = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=1000)
    current = MarstekDeviceStatus(device_ip="192.168.1.10", total_pv_energy=None)

    held = hold_glitches(previous, current, falling_energy_polls=FALLING_ENERGY_POLLS)
    assert held.total_pv_energy == 1000


@pytest.mark.parametrize(
    ("response", "match"),
    [
        pytest.param({"bat_soc": "98"}, "bat_soc", id="numeric_string"),
        pytest.param({"ongrid_power": True}, "ongrid_power", id="boolean_power"),
        pytest.param({"mode": "unsupported"}, "mode", id="unknown_mode"),
        pytest.param({"pv1_state": 2}, "pv1_state", id="unknown_pv_state"),
        pytest.param(
            {"total_pv_energy": "12345"},
            "total_pv_energy",
            id="numeric_energy_string",
        ),
    ],
)
def test_device_status_rejects_invalid_protocol_values(
    response: dict[str, object], match: str
) -> None:
    """Test invalid protocol values are rejected by the library."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10")

    with pytest.raises(TypeError, match=match):
        if "pv1_state" in response:
            status.with_pv_status_response(response)
        elif "total_pv_energy" in response:
            status.with_es_status_response(response)
        else:
            status.with_es_mode_response(response)


@pytest.mark.parametrize(
    "value",
    [pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf")],
)
def test_device_status_rejects_non_finite_numbers(value: float) -> None:
    """NaN and Infinity are not valid sensor readings and are rejected."""
    status = MarstekDeviceStatus(device_ip="192.168.1.10")
    with pytest.raises(ValueError, match="finite"):
        status.with_es_status_response({"bat_soc": value})


def test_manual_slot_rejects_time_num_out_of_range() -> None:
    """time_num must stay within the documented slot range."""
    with pytest.raises(ValueError, match="time_num"):
        MarstekManualSlot(
            time_num=MANUAL_SLOT_MAX + 1,
            start_time="00:00",
            end_time="01:00",
            week_set=0,
            power=100,
        )


def test_manual_slot_rejects_negative_week_set() -> None:
    """week_set must be non-negative."""
    with pytest.raises(ValueError, match="week_set"):
        MarstekManualSlot(
            time_num=0,
            start_time="00:00",
            end_time="01:00",
            week_set=-1,
            power=100,
        )


def test_manual_slot_rejects_empty_times() -> None:
    """start_time and end_time must be non-empty."""
    with pytest.raises(ValueError, match="start_time"):
        MarstekManualSlot(
            time_num=0,
            start_time="",
            end_time="01:00",
            week_set=0,
            power=100,
        )


def test_manual_slot_rejects_unknown_manual_set() -> None:
    """manual_set must be one of the documented 0..3 values."""
    with pytest.raises(ValueError, match="manual_set"):
        MarstekManualSlot(
            time_num=0,
            start_time="00:00",
            end_time="01:00",
            week_set=0,
            power=100,
            manual_set=MANUAL_SET_AUTO + 1,
        )


def test_manual_slot_accepts_valid_slot() -> None:
    """A slot within all ranges constructs and serializes without error."""
    slot = MarstekManualSlot(
        time_num=MANUAL_SLOT_MAX,
        start_time="00:00",
        end_time="23:59",
        week_set=127,
        power=100,
        manual_set=MANUAL_SET_AUTO,
    )
    assert slot.to_payload()["manual_set"] == MANUAL_SET_AUTO


def test_mode_config_rejects_unknown_mode() -> None:
    """mode must be one of the documented mode names."""
    with pytest.raises(ValueError, match="mode"):
        MarstekModeConfig(mode="bogus")


def test_mode_config_requires_slots_in_manual() -> None:
    """manual mode requires at least one manual slot."""
    with pytest.raises(ValueError, match="manual mode"):
        MarstekModeConfig(mode=MODE_MANUAL)


def test_mode_config_requires_passive_config_in_passive() -> None:
    """passive mode requires a passive config."""
    with pytest.raises(ValueError, match="passive mode"):
        MarstekModeConfig(mode=MODE_PASSIVE)


def test_mode_config_accepts_valid_auto() -> None:
    """A valid auto config serializes without error."""
    config = MarstekModeConfig(mode=MODE_AUTO)
    assert config.to_payload()["mode"] == "Auto"
