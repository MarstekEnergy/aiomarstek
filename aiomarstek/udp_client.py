"""UDP client for Marstek device communication."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from .command_builder import discover, get_es_mode, get_es_status, get_pv_status
from .const import DEFAULT_UDP_PORT, DISCOVERY_TIMEOUT
from .models import MarstekDeviceInfo, MarstekDeviceStatus

_LOGGER = logging.getLogger(__name__)
_SOCKET_FACTORY = socket.socket


class MarstekUDPClient:
    """UDP client for Marstek device communication."""

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        broadcast_addresses: Iterable[str] | None = None,
    ) -> None:
        """Initialize UDP client."""
        self._port = port
        self._socket: socket.socket | None = None
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._response_cache: dict[int, dict[str, Any]] = {}
        self._listen_task: asyncio.Task | None = None
        self._discovery_cache: list[MarstekDeviceInfo] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0
        self._local_send_ip: str = "0.0.0.0"
        self._polling_paused: dict[str, bool] = {}
        self._polling_lock: asyncio.Lock = asyncio.Lock()
        self._broadcast_addresses = list(broadcast_addresses or ["255.255.255.255"])
        self._es_mode_device_ids: dict[str, int] = {}
        self._pv_status_supported: dict[str, bool] = {}

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
        self._pending_requests[request_id] = future

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
        """Listen for UDP responses."""
        if not self._socket:
            return

        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
                response_text = data.decode("utf-8")
                try:
                    response = json.loads(response_text)
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

                if request_id:
                    self._response_cache[request_id] = {
                        "response": response,
                        "addr": addr,
                        "timestamp": loop.time(),
                    }

                if request_id and request_id in self._pending_requests:
                    future = self._pending_requests.pop(request_id)
                    if not future.done():
                        future.set_result(response)
            except asyncio.CancelledError:
                break
            except OSError as err:
                _LOGGER.error("Error receiving UDP response: %s", err)
                await asyncio.sleep(1)

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
        start_time = asyncio.get_running_loop().time()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future

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
            try:
                while (loop.time() - start_time) < timeout:
                    if request_id in self._response_cache:
                        cached_response = self._response_cache[request_id]
                        responses.append(cached_response["response"])
                        _LOGGER.debug(
                            "Broadcast ID:%s received %d response(s)",
                            request_id,
                            len(responses),
                        )
                        del self._response_cache[request_id]

                    await asyncio.sleep(0.1)

                    if (loop.time() - start_time) >= timeout:
                        _LOGGER.debug("Broadcast ID:%s wait timeout", request_id)
                        break
            except OSError as err:
                _LOGGER.error("Error waiting for response: %s", err)
        finally:
            self._pending_requests.pop(request_id, None)

        _LOGGER.info("Broadcast discovery finished, %d responses", len(responses))
        return responses

    async def discover_devices(
        self,
        use_cache: bool = True,
        broadcast_addresses: Iterable[str] | None = None,
    ) -> list[MarstekDeviceInfo]:
        """Discover Marstek devices on network and deduplicate results."""
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

            for response in responses:
                result = response.get("result")
                if isinstance(result, dict):
                    device_info = MarstekDeviceInfo.from_response(result)
                    device_id = (
                        device_info.ip
                        or device_info.ble_mac
                        or device_info.wifi_mac
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
        except OSError as err:
            _LOGGER.error("Device discovery failed: %s", err)

        self._discovery_cache = devices.copy()
        self._cache_timestamp = asyncio.get_running_loop().time()

        _LOGGER.info("Device discovery finished, %d unique devices", len(devices))
        return devices

    async def pause_polling(self, device_ip: str) -> None:
        """Pause polling for a specific device."""
        async with self._polling_lock:
            self._polling_paused[device_ip] = True
            _LOGGER.info("Polling paused for device: %s", device_ip)

    async def resume_polling(self, device_ip: str) -> None:
        """Resume polling for a specific device."""
        async with self._polling_lock:
            self._polling_paused[device_ip] = False
            _LOGGER.info("Polling resumed for device: %s", device_ip)

    def is_polling_paused(self, device_ip: str) -> bool:
        """Check if polling is paused for a specific device."""
        return self._polling_paused.get(device_ip, False)

    async def send_request_with_polling_control(
        self,
        message: str,
        target_ip: str,
        target_port: int | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Send request with polling control."""
        await self.pause_polling(target_ip)

        try:
            return await self.send_request(
                message,
                target_ip,
                target_port,
                timeout,
                quiet_on_timeout=True,
            )
        finally:
            await self.resume_polling(target_ip)

    async def get_device_status(
        self,
        device_ip: str,
        *,
        previous_data: MarstekDeviceStatus | None = None,
        timeout: float = 2.5,
        delay_ms: int = 2000,
    ) -> MarstekDeviceStatus:
        """Fetch device status and PV data."""
        result_data = previous_data or MarstekDeviceStatus(device_ip=device_ip)

        async def delay(ms: int) -> None:
            await asyncio.sleep(ms / 1000.0)

        async def es_status_request() -> None:
            nonlocal result_data
            try:
                es_status_result = await self.send_request(
                    get_es_status(0),
                    device_ip,
                    timeout=timeout,
                    quiet_on_timeout=True,
                )
            except (TimeoutError, OSError):
                return
            status_data = es_status_result.get("result")
            if not isinstance(status_data, dict):
                return
            result_data = result_data.with_es_status_response(status_data)

        async def es_mode_request() -> None:
            nonlocal result_data
            preferred_id = self._es_mode_device_ids.get(device_ip, 1)
            device_ids = (preferred_id, 1 - preferred_id)
            for device_id in device_ids:
                try:
                    mode_result = await self.send_request(
                        get_es_mode(device_id),
                        device_ip,
                        timeout=timeout,
                        quiet_on_timeout=True,
                    )
                except (TimeoutError, OSError):
                    continue
                mode_data = mode_result.get("result")
                if not isinstance(mode_data, dict):
                    continue
                self._es_mode_device_ids[device_ip] = device_id
                result_data = result_data.with_es_mode_response(mode_data)
                return

        async def pv_status_request() -> None:
            nonlocal result_data
            if self._pv_status_supported.get(device_ip) is False:
                return
            try:
                pv_status_result = await self.send_request(
                    get_pv_status(0),
                    device_ip,
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
            result_data = result_data.with_pv_status_response(pv_data)

        await es_status_request()
        await es_mode_request()
        if self._pv_status_supported.get(device_ip) is not False:
            await delay(delay_ms)
            await pv_status_request()

        return result_data

    async def get_device_info(
        self, device_ip: str, *, timeout: float = 5.0
    ) -> MarstekDeviceInfo:
        """Fetch device information from a specific host."""
        response = await self.send_request(discover(), device_ip, timeout=timeout)
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            raise TypeError("No device information returned")
        return MarstekDeviceInfo.from_response(result, device_ip)
