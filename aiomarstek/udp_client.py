"""UDP client for Marstek device communication."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from .command_builder import (
    discover,
    get_battery_status,
    get_ble_status,
    get_em_status,
    get_es_mode,
    get_es_status,
    get_pv_status,
    get_wifi_status,
    set_ble_adv,
    set_dod,
    set_es_mode,
    set_led,
    set_power_version,
)
from .command_builder import (
    factory_reset as factory_reset_command,
)
from .const import (
    CMD_BATTERY_STATUS,
    CMD_BLE_STATUS,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
    DEFAULT_UDP_PORT,
    DISCOVERY_TIMEOUT,
    DOD_MAX,
    DOD_MIN,
    EXTRAS_INTERVAL,
    POWER_VERSIONS,
    TRUTHY_SET_RESULTS,
)
from .models import (
    MONOTONIC_KEYS,
    MarstekDeviceInfo,
    MarstekDeviceStatus,
    MarstekModeConfig,
    hold_glitches,
)

_LOGGER = logging.getLogger(__name__)
_SOCKET_FACTORY = socket.socket


class MarstekNoResponse(Exception):
    """The device did not answer any status request."""


class MarstekSetError(Exception):
    """The device did not accept a configuration command."""

    def __init__(self, device_ip: str, method: str, reason: str) -> None:
        """Initialize the error with the failing command context."""
        super().__init__(f"{method} to {device_ip} failed: {reason}")
        self.device_ip = device_ip
        self.method = method
        self.reason = reason


class MarstekUDPClient:
    """UDP client for Marstek device communication."""

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        broadcast_addresses: Iterable[str] | None = None,
        *,
        verify_source_ip: bool = True,
    ) -> None:
        """Initialize UDP client.

        ``verify_source_ip`` drops a unicast response whose source address
        does not match the device the request was sent to, so another host
        on the LAN cannot forge a reply by guessing or observing a request
        id. Defaults to True; loopback tests and deployments behind NAT that
        rewrites source addresses may disable it.
        """
        self._port = port
        self._verify_source_ip = verify_source_ip
        self._socket: socket.socket | None = None
        self._pending_requests: dict[int, tuple[asyncio.Future, str]] = {}
        self._broadcast_responses: dict[int, list[dict[str, Any]]] = {}
        self._listen_task: asyncio.Task | None = None
        self._discovery_cache: list[MarstekDeviceInfo] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0
        self._local_send_ip: str = "0.0.0.0"
        self._polling_pause_count: dict[str, int] = {}
        self._status_locks: dict[str, asyncio.Lock] = {}
        self._polling_lock: asyncio.Lock = asyncio.Lock()
        self._broadcast_addresses = list(broadcast_addresses or ["255.255.255.255"])
        self._es_mode_device_ids: dict[str, int] = {}
        self._pv_status_supported: dict[str, bool] = {}
        self._last_results: dict[str, dict[str, dict[str, Any]]] = {}
        self._unsupported_methods: dict[str, set[str]] = {}
        self._extras_age: dict[str, int] = {}
        self._last_status: dict[str, MarstekDeviceStatus] = {}
        self._zero_soc_polls: dict[str, int] = {}
        self._falling_energy_polls: dict[str, dict[str, int]] = {}

    def get_last_results(self, device_ip: str) -> dict[str, dict[str, Any]]:
        """Return the latest raw result of each status request, by method."""
        return dict(self._last_results.get(device_ip, {}))

    def is_pv_status_supported(self, device_ip: str) -> bool | None:
        """Return whether the device answers PV.GetStatus, or None if unknown."""
        return self._pv_status_supported.get(device_ip)

    def _record_response(
        self, device_ip: str, method: str, result: dict[str, Any]
    ) -> None:
        """Record the raw result of a status request, for diagnostics."""
        self._last_results.setdefault(device_ip, {})[method] = result

    def set_broadcast_addresses(self, addresses: Iterable[str]) -> None:
        """Set broadcast addresses used for discovery."""
        self._broadcast_addresses = list(addresses)

    def get_discovery_cache(self) -> list[MarstekDeviceInfo] | None:
        """Return a copy of the discovery cache."""
        if self._discovery_cache is None:
            return None
        return self._discovery_cache.copy()

    async def async_setup(self) -> None:
        """Setup UDP socket."""
        if self._socket is not None:
            return

        self._socket = _SOCKET_FACTORY(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 30000))
        _LOGGER.debug(
            "UDP client bound to %s:%s",
            self._socket.getsockname()[0],
            self._socket.getsockname()[1],
        )

    async def async_cleanup(self) -> None:
        """Cleanup UDP socket."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_task

        if self._socket:
            self._socket.close()
            self._socket = None

    def _is_cache_valid(self) -> bool:
        """Check if cache is valid."""
        if self._discovery_cache is None:
            return False
        current_time = asyncio.get_running_loop().time()
        return (current_time - self._cache_timestamp) < self._cache_duration

    def clear_discovery_cache(self) -> None:
        """Clear discovery cache."""
        self._discovery_cache = None
        self._cache_timestamp = 0
        _LOGGER.debug("Device discovery cache cleared")

    async def _send_udp_message(
        self, message: str, target_ip: str, target_port: int | None = None
    ) -> None:
        """Send UDP message to the specified target."""
        if not self._socket:
            await self.async_setup()
        assert self._socket is not None

        try:
            data = message.encode("utf-8")
            self._socket.sendto(data, (target_ip, target_port or self._port))
            _LOGGER.debug(
                "Send: %s:%d <- %s:%d | %s",
                target_ip,
                target_port or self._port,
                self._local_send_ip,
                self._port,
                message,
            )
        except Exception as err:
            _LOGGER.error("Failed to send UDP message: %s", err)
            raise

    async def send_request(
        self,
        message: str,
        target_ip: str,
        target_port: int | None = None,
        timeout: float = 5.0,
        *,
        quiet_on_timeout: bool = False,
    ) -> dict[str, Any]:
        """Send unicast request and wait for response."""
        if not self._socket:
            await self.async_setup()

        try:
            message_obj = json.loads(message)
            request_id = message_obj["id"]
        except (json.JSONDecodeError, KeyError) as err:
            _LOGGER.error("Invalid message format: %s", err)
            raise ValueError(f"Invalid message format: {err}") from err

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = (future, target_ip)

        try:
            if not self._listen_task or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._listen_for_responses())

            await self._send_udp_message(message, target_ip, target_port)

            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError as err:
                if quiet_on_timeout or self.is_polling_paused(target_ip):
                    _LOGGER.debug(
                        "Request timeout: %s:%d (quiet)",
                        target_ip,
                        target_port or self._port,
                    )
                else:
                    _LOGGER.warning(
                        "Request timeout: %s:%d",
                        target_ip,
                        target_port or self._port,
                    )
                raise TimeoutError(
                    f"Request timeout to {target_ip}:{target_port or self._port}"
                ) from err
        finally:
            self._pending_requests.pop(request_id, None)

    async def _listen_for_responses(self) -> None:
        """Listen for UDP responses.

        Receiving and parsing are split: a single malformed packet (bad
        UTF-8 or invalid JSON) is logged and dropped so it can never kill
        the listener or leave pending requests stuck until their timeout.
        """
        if not self._socket:
            return

        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
            except asyncio.CancelledError:
                break
            except OSError as err:
                _LOGGER.error("Error receiving UDP response: %s", err)
                await asyncio.sleep(1)
                continue

            try:
                response_text = data.decode("utf-8")
                response = json.loads(response_text)
            except UnicodeDecodeError:
                _LOGGER.debug(
                    "Dropping non-UTF-8 UDP packet from %s:%d", addr[0], addr[1]
                )
                continue
            except json.JSONDecodeError:
                response = {"raw": response_text}

            request_id = response.get("id") if isinstance(response, dict) else None

            _LOGGER.debug(
                "Recv: %s:%d -> %s:%d | %s",
                addr[0],
                addr[1],
                self._local_send_ip,
                self._port,
                json.dumps(response, ensure_ascii=False),
            )

            # Only keep responses someone is waiting for; anything else is
            # dropped so unsolicited or late packets cannot accumulate.
            if request_id and request_id in self._broadcast_responses:
                self._broadcast_responses[request_id].append(response)
            elif request_id and request_id in self._pending_requests:
                future, expected_source = self._pending_requests[request_id]
                # A unicast response must come from the device we asked; a
                # packet from another host with a guessed or observed id is
                # dropped so it cannot forge a status reply or fake a write
                # success. The entry is left in place for a legitimate reply.
                # Source verification can be disabled for loopback tests or
                # NAT setups that rewrite the source address.
                if self._verify_source_ip and addr[0] != expected_source:
                    _LOGGER.warning(
                        "Dropping unicast response %d from %s:%d: expected %s",
                        request_id,
                        addr[0],
                        addr[1],
                        expected_source,
                    )
                    continue
                self._pending_requests.pop(request_id)
                if not future.done():
                    future.set_result(response)

    async def send_broadcast_request(
        self,
        message: str,
        timeout: float = DISCOVERY_TIMEOUT,
        broadcast_addresses: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Send broadcast request and collect all responses."""
        if not self._socket:
            await self.async_setup()

        try:
            message_obj = json.loads(message)
            request_id = message_obj["id"]
        except (json.JSONDecodeError, KeyError) as err:
            _LOGGER.error("Invalid message format: %s", err)
            return []

        responses: list[dict[str, Any]] = []
        self._broadcast_responses[request_id] = responses

        try:
            if not self._listen_task or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._listen_for_responses())

            targets = list(broadcast_addresses or self._broadcast_addresses)
            _LOGGER.debug("Broadcast targets: %s", targets)

            for address in targets:
                await self._send_udp_message(message, address, self._port)
                _LOGGER.debug("Sent to %s:%s", address, self._port)

            _LOGGER.debug("Broadcast to %d interfaces", len(targets))
            _LOGGER.debug("Broadcast payload: %s", message)
            _LOGGER.debug("Target port: %s", self._port)

            _LOGGER.debug("Start waiting for responses, timeout: %d s", timeout)
            await asyncio.sleep(timeout)
            _LOGGER.debug(
                "Broadcast ID:%s received %d response(s)", request_id, len(responses)
            )
        finally:
            self._broadcast_responses.pop(request_id, None)

        _LOGGER.info("Broadcast discovery finished, %d responses", len(responses))
        return responses

    async def discover_devices(
        self,
        use_cache: bool = True,
        broadcast_addresses: Iterable[str] | None = None,
    ) -> list[MarstekDeviceInfo]:
        """Discover Marstek devices on network and deduplicate results.

        A socket or broadcast failure is raised to the caller instead of
        being silently returned as an empty list, so callers can tell "no
        devices" (empty list, cached) apart from "network error" (raised,
        not cached and retried on the next call).
        """
        if use_cache and self._is_cache_valid():
            _LOGGER.debug("Using cached discovery results")
            assert self._discovery_cache is not None
            return self._discovery_cache.copy()

        devices: list[MarstekDeviceInfo] = []
        seen_devices: set[str] = set()

        try:
            responses = await self.send_broadcast_request(
                discover(),
                broadcast_addresses=broadcast_addresses,
            )
        except OSError as err:
            # A socket/broadcast failure is not "no devices": do not cache
            # it so the next call can retry, and surface it to the caller.
            _LOGGER.error("Device discovery failed: %s", err)
            self._discovery_cache = None
            self._cache_timestamp = 0
            raise

        for response in responses:
            result = response.get("result")
            if isinstance(result, dict):
                device_info = MarstekDeviceInfo.from_response(result)
                device_id = (
                    device_info.ip
                    or device_info.ble_mac
                    or f"device_{int(asyncio.get_running_loop().time())}_{hash(device_info) % 10000}"
                )

                if device_id in seen_devices:
                    _LOGGER.debug(
                        "Skip duplicate device: %s (IP: %s, BLE_MAC: %s, WiFi_MAC: %s)",
                        device_id,
                        device_info.ip,
                        device_info.ble_mac,
                        device_info.wifi_mac,
                    )
                    continue

                seen_devices.add(device_id)
                _LOGGER.debug(
                    "Add device: %s (IP: %s, BLE_MAC: %s, WiFi_MAC: %s)",
                    device_id,
                    device_info.ip,
                    device_info.ble_mac,
                    device_info.wifi_mac,
                )

                devices.append(device_info)
                _LOGGER.info(
                    "Discovered device: Type=%s, Version=%s, WiFi=%s, IP=%s, MAC=%s",
                    device_info.device_type,
                    device_info.version,
                    device_info.wifi_name,
                    device_info.ip,
                    device_info.mac,
                )

        self._discovery_cache = devices.copy()
        self._cache_timestamp = asyncio.get_running_loop().time()

        _LOGGER.info("Device discovery finished, %d unique devices", len(devices))
        return devices

    async def pause_polling(self, device_ip: str) -> None:
        """Pause polling for a specific device.

        Uses a count so overlapping write commands do not resume polling
        before every one of them has finished: each pause adds one, each
        resume removes one, and polling only resumes when the count hits 0.
        """
        async with self._polling_lock:
            self._polling_pause_count[device_ip] = (
                self._polling_pause_count.get(device_ip, 0) + 1
            )
            _LOGGER.info(
                "Polling paused for device: %s (depth %d)",
                device_ip,
                self._polling_pause_count[device_ip],
            )

    async def resume_polling(self, device_ip: str) -> None:
        """Resume polling for a specific device."""
        async with self._polling_lock:
            current = self._polling_pause_count.get(device_ip, 0)
            if current <= 1:
                self._polling_pause_count.pop(device_ip, None)
                _LOGGER.info("Polling resumed for device: %s", device_ip)
            else:
                self._polling_pause_count[device_ip] = current - 1
                _LOGGER.info(
                    "Polling still paused for device: %s (depth %d)",
                    device_ip,
                    current - 1,
                )

    def is_polling_paused(self, device_ip: str) -> bool:
        """Check if polling is paused for a specific device."""
        return self._polling_pause_count.get(device_ip, 0) > 0

    async def send_request_with_polling_control(
        self,
        message: str,
        target_ip: str,
        target_port: int | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Send a configuration command, serialized against status polls.

        Pauses polling first so any call already past the pause check
        knows to skip, then acquires the per-device status lock so the
        command waits for an in-flight poll to finish and blocks later
        polls until it is done. The try/finally wraps the lock so that a
        cancellation while waiting for the lock still resumes polling.
        """
        await self.pause_polling(target_ip)
        try:
            async with self._device_status_lock(target_ip):
                return await self.send_request(
                    message,
                    target_ip,
                    target_port,
                    timeout,
                    quiet_on_timeout=True,
                )
        finally:
            await self.resume_polling(target_ip)

    def _is_method_supported(self, device_ip: str, method: str) -> bool:
        """Return whether the device answers the given method."""
        return method not in self._unsupported_methods.get(device_ip, frozenset())

    def _mark_method_unsupported(self, device_ip: str, method: str) -> None:
        """Record that the device rejects the given method."""
        self._unsupported_methods.setdefault(device_ip, set()).add(method)
        _LOGGER.debug("Method %s not supported by %s", method, device_ip)

    async def _async_query_component(
        self,
        message: str,
        device_ip: str,
        method: str,
        *,
        port: int | None = None,
        timeout: float,
    ) -> dict[str, Any] | None:
        """Send a status query and return its result.

        Returns None when the device stays silent or answers with a JSON-RPC
        error; a "method not found" error also marks the method as
        unsupported so it is not queried again.
        """
        try:
            response = await self.send_request(
                message,
                device_ip,
                target_port=port,
                timeout=timeout,
                quiet_on_timeout=True,
            )
        except (TimeoutError, OSError):
            return None
        result = response.get("result")
        if not isinstance(result, dict):
            error = response.get("error")
            if isinstance(error, dict) and error.get("code") == -32601:
                self._mark_method_unsupported(device_ip, method)
            return None
        self._record_response(device_ip, method, result)
        return result

    def _extras_due(self, device_ip: str) -> bool:
        """Return whether the slow-changing components are due for a refresh."""
        age = self._extras_age.get(device_ip, 0) + 1
        if age >= EXTRAS_INTERVAL:
            self._extras_age[device_ip] = 0
            return True
        self._extras_age[device_ip] = age
        return False

    async def get_device_status(
        self,
        device_ip: str,
        *,
        port: int | None = None,
        previous_data: MarstekDeviceStatus | None = None,
        timeout: float = 2.5,
        delay_ms: int = 2000,
        include_pv: bool = True,
        include_battery: bool = True,
        include_extras: bool = True,
    ) -> MarstekDeviceStatus:
        """Fetch device status and component data.

        Concurrent calls for the same device are serialized so their
        snapshots cannot overwrite each other. The pause check is done
        inside the lock: a write command may pause polling while this call
        is waiting for the lock, and the re-check then makes the call raise
        ``MarstekNoResponse`` instead of overlapping the in-flight write.

        See ``_get_device_status_unlocked`` for the per-request behaviour.
        """
        async with self._device_status_lock(device_ip):
            if self.is_polling_paused(device_ip):
                _LOGGER.debug(
                    "Status poll skipped for %s: polling paused for an in-flight write",
                    device_ip,
                )
                raise MarstekNoResponse(device_ip)
            return await self._get_device_status_unlocked(
                device_ip,
                port=port,
                previous_data=previous_data,
                timeout=timeout,
                delay_ms=delay_ms,
                include_pv=include_pv,
                include_battery=include_battery,
                include_extras=include_extras,
            )

    def _device_status_lock(self, device_ip: str) -> asyncio.Lock:
        """Return the per-device lock that serializes status polls."""
        lock = self._status_locks.get(device_ip)
        if lock is None:
            lock = asyncio.Lock()
            self._status_locks[device_ip] = lock
        return lock

    async def _get_device_status_unlocked(
        self,
        device_ip: str,
        *,
        port: int | None = None,
        previous_data: MarstekDeviceStatus | None = None,
        timeout: float = 2.5,
        delay_ms: int = 2000,
        include_pv: bool = True,
        include_battery: bool = True,
        include_extras: bool = True,
    ) -> MarstekDeviceStatus:
        """Fetch device status and component data.

        ``port`` overrides the client's default device UDP port for every
        request in this poll, so a device configured for a non-default port
        (the Open API documents 49152-65535 as the recommended range) can be
        queried without a dedicated client. The local listen socket stays
        fixed, so responses always come back to the same port.

        Queries ES.GetStatus, ES.GetMode and Bat.GetStatus on every call,
        PV.GetStatus for devices with DC PV inputs, and the slow-changing
        WiFi, BLE and EM components every ``EXTRAS_INTERVAL`` calls.

        Raises MarstekNoResponse when the device does not answer any of the
        status requests; the caller then keeps the previous data.
        """
        result_data = (
            previous_data
            if previous_data is not None
            else self._last_status.get(device_ip)
        ) or MarstekDeviceStatus(device_ip=device_ip)
        answered = False
        # The recorded results double as a freshness snapshot: each poll
        # starts from scratch, so afterwards they hold exactly the methods
        # that answered this poll. A fully silent poll restores the previous
        # snapshot, so diagnostics keep the last raw replies received.
        snapshot = self._last_results.get(device_ip, {})
        self._last_results[device_ip] = {}

        async def delay(ms: int) -> None:
            await asyncio.sleep(ms / 1000.0)

        async def es_status_request() -> None:
            nonlocal result_data, answered
            try:
                es_status_result = await self.send_request(
                    get_es_status(0),
                    device_ip,
                    target_port=port,
                    timeout=timeout,
                    quiet_on_timeout=True,
                )
            except (TimeoutError, OSError):
                return
            status_data = es_status_result.get("result")
            if not isinstance(status_data, dict):
                return
            self._record_response(device_ip, CMD_ES_STATUS, status_data)
            answered = True
            result_data = result_data.with_es_status_response(status_data)

        async def es_mode_request() -> None:
            nonlocal result_data, answered
            preferred_id = self._es_mode_device_ids.get(device_ip, 1)
            device_ids = (preferred_id, 1 - preferred_id)
            for device_id in device_ids:
                try:
                    mode_result = await self.send_request(
                        get_es_mode(device_id),
                        device_ip,
                        target_port=port,
                        timeout=timeout,
                        quiet_on_timeout=True,
                    )
                except (TimeoutError, OSError):
                    continue
                mode_data = mode_result.get("result")
                if not isinstance(mode_data, dict):
                    continue
                self._es_mode_device_ids[device_ip] = device_id
                self._record_response(device_ip, CMD_ES_MODE, mode_data)
                answered = True
                result_data = result_data.with_es_mode_response(mode_data)
                return

        async def battery_status_request() -> None:
            nonlocal result_data, answered
            if not include_battery:
                return
            if not self._is_method_supported(device_ip, CMD_BATTERY_STATUS):
                return
            result = await self._async_query_component(
                get_battery_status(0),
                device_ip,
                CMD_BATTERY_STATUS,
                port=port,
                timeout=timeout,
            )
            if result is None:
                return
            answered = True
            result_data = result_data.with_battery_status_response(result)

        async def pv_status_request() -> None:
            nonlocal result_data, answered
            if not include_pv or self._pv_status_supported.get(device_ip) is False:
                return
            try:
                pv_status_result = await self.send_request(
                    get_pv_status(0),
                    device_ip,
                    target_port=port,
                    timeout=timeout,
                )
            except (TimeoutError, OSError):
                return
            pv_data = pv_status_result.get("result")
            if not isinstance(pv_data, dict):
                error = pv_status_result.get("error")
                if isinstance(error, dict) and error.get("code") == -32601:
                    self._pv_status_supported[device_ip] = False
                return
            self._pv_status_supported[device_ip] = True
            self._record_response(device_ip, CMD_PV_GET_STATUS, pv_data)
            answered = True
            result_data = result_data.with_pv_status_response(pv_data)

        async def extras_request() -> None:
            nonlocal result_data, answered
            if self._is_method_supported(device_ip, CMD_WIFI_STATUS):
                result = await self._async_query_component(
                    get_wifi_status(0),
                    device_ip,
                    CMD_WIFI_STATUS,
                    port=port,
                    timeout=timeout,
                )
                if result is not None:
                    answered = True
                    result_data = result_data.with_wifi_status_response(result)
            if self._is_method_supported(device_ip, CMD_BLE_STATUS):
                result = await self._async_query_component(
                    get_ble_status(0),
                    device_ip,
                    CMD_BLE_STATUS,
                    port=port,
                    timeout=timeout,
                )
                if result is not None:
                    answered = True
                    result_data = result_data.with_ble_status_response(result)
            if self._is_method_supported(device_ip, CMD_EM_STATUS):
                result = await self._async_query_component(
                    get_em_status(0),
                    device_ip,
                    CMD_EM_STATUS,
                    port=port,
                    timeout=timeout,
                )
                if result is not None:
                    answered = True
                    result_data = result_data.with_em_status_response(result)

        await es_status_request()
        await es_mode_request()
        await battery_status_request()
        if include_pv and self._pv_status_supported.get(device_ip) is not False:
            await delay(delay_ms)
            await pv_status_request()
        if include_extras and self._extras_due(device_ip):
            await delay(delay_ms)
            await extras_request()

        if not answered:
            self._last_results[device_ip] = snapshot
            raise MarstekNoResponse(device_ip)
        return self._smooth_status(device_ip, previous_data, result_data)

    def _smooth_status(
        self,
        device_ip: str,
        previous_data: MarstekDeviceStatus | None,
        current: MarstekDeviceStatus,
    ) -> MarstekDeviceStatus:
        """Apply glitch protection and update per-device streak counters.

        The previous reading is the caller-supplied one when available
        (Home Assistant's coordinator passes the last poll) and otherwise
        the last smoothed reading kept by the client, so protection works
        even when a caller never passes ``previous_data``. Raw responses
        stay available via ``get_last_results`` for diagnostics; only the
        returned status is smoothed. A failed poll raises before reaching
        here, so a streak survives an unanswered poll unchanged.
        """
        previous = (
            previous_data
            if previous_data is not None
            else self._last_status.get(device_ip)
        )
        if previous is None:
            self._last_status[device_ip] = current
            return current

        zero_soc = self._zero_soc_polls.get(device_ip, 0)
        falling = self._falling_energy_polls.get(device_ip, {})
        smoothed = hold_glitches(previous, current, zero_soc, falling)

        # Update streak counters from the raw current reading against the
        # previous one. A missing value leaves its streak untouched; a
        # recovered or stable reading clears it.
        if current.battery_soc == 0:
            self._zero_soc_polls[device_ip] = zero_soc + 1
        elif current.battery_soc is not None:
            self._zero_soc_polls[device_ip] = 0

        per_key = self._falling_energy_polls.setdefault(device_ip, {})
        for key in MONOTONIC_KEYS:
            old = getattr(previous, key)
            new = getattr(current, key)
            if new is None:
                continue
            if old is None:
                per_key[key] = 0
            elif new < old:
                per_key[key] = per_key.get(key, 0) + 1
            else:
                per_key[key] = 0

        self._last_status[device_ip] = smoothed
        return smoothed

    async def _async_send_set_command(
        self,
        message: str,
        device_ip: str,
        method: str,
        *,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Send a configuration command and verify its set_result."""
        try:
            response = await self.send_request_with_polling_control(
                message, device_ip, target_port=port, timeout=timeout
            )
        except (TimeoutError, OSError) as err:
            raise MarstekSetError(
                device_ip, method, "the device did not respond"
            ) from err

        result = response.get("result")
        if not isinstance(result, dict):
            raise MarstekSetError(device_ip, method, "the device returned no result")
        if result.get("set_result") not in TRUTHY_SET_RESULTS:
            raise MarstekSetError(device_ip, method, "the device rejected the command")

    async def set_device_mode(
        self,
        device_ip: str,
        config: MarstekModeConfig,
        *,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Set the device operating mode via ES.SetMode."""
        await self._async_send_set_command(
            set_es_mode(config), device_ip, CMD_ES_SET_MODE, port=port, timeout=timeout
        )

    async def set_dod(
        self,
        device_ip: str,
        value: int,
        *,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Set the discharge depth (30-88) via DOD.SET."""
        if not DOD_MIN <= value <= DOD_MAX:
            raise ValueError(f"discharge depth must be between {DOD_MIN} and {DOD_MAX}")
        await self._async_send_set_command(
            set_dod(value), device_ip, "DOD.SET", port=port, timeout=timeout
        )

    async def set_bluetooth_advertising(
        self,
        device_ip: str,
        enabled: bool,
        *,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Turn Bluetooth advertising on or off via Ble.Adv."""
        await self._async_send_set_command(
            set_ble_adv(enabled), device_ip, "Ble.Adv", port=port, timeout=timeout
        )

    async def set_led(
        self, device_ip: str, on: bool, *, port: int | None = None, timeout: float = 5.0
    ) -> None:
        """Turn the front-panel LED on or off via Led.Ctrl."""
        await self._async_send_set_command(
            set_led(on), device_ip, "Led.Ctrl", port=port, timeout=timeout
        )

    async def set_power_version(
        self,
        device_ip: str,
        version: int,
        *,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Set the charge/discharge power version via Set.Ver."""
        if version not in POWER_VERSIONS:
            raise ValueError(
                f"power version must be one of {', '.join(map(str, POWER_VERSIONS))}"
            )
        await self._async_send_set_command(
            set_power_version(version), device_ip, "Set.Ver", port=port, timeout=timeout
        )

    async def factory_reset(
        self,
        device_ip: str,
        *,
        keep_accumulated_data: bool = False,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Reset the device to factory settings via Reset.Factory.

        .. warning::

           By default (``keep_accumulated_data=False``) this clears the
           lifetime energy counters and other accumulated data, not only the
           configuration. Pass ``keep_accumulated_data=True`` to preserve
           them. This is a destructive, high-consequence operation.
        """
        await self._async_send_set_command(
            factory_reset_command(keep_accumulated_data),
            device_ip,
            "Reset.Factory",
            port=port,
            timeout=timeout,
        )

    async def get_device_info(
        self, device_ip: str, *, port: int | None = None, timeout: float = 5.0
    ) -> MarstekDeviceInfo:
        """Fetch device information from a specific host."""
        response = await self.send_request(
            discover(), device_ip, target_port=port, timeout=timeout
        )
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            raise TypeError("No device information returned")
        return MarstekDeviceInfo.from_response(result, device_ip)
