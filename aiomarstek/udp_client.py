"""UDP client for Marstek device communication."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from .command_builder import discover, get_es_mode, get_pv_status
from .const import DEFAULT_UDP_PORT, DISCOVERY_TIMEOUT

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
        self._discovery_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0
        self._local_send_ip: str = "0.0.0.0"
        self._polling_paused: dict[str, bool] = {}
        self._polling_lock: asyncio.Lock = asyncio.Lock()
        self._broadcast_addresses = list(broadcast_addresses or ["255.255.255.255"])

    def set_broadcast_addresses(self, addresses: Iterable[str]) -> None:
        """Set broadcast addresses used for discovery."""
        self._broadcast_addresses = list(addresses)

    def get_discovery_cache(self) -> list[dict[str, Any]] | None:
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
    ) -> list[dict[str, Any]]:
        """Discover Marstek devices on network and deduplicate results."""
        if use_cache and self._is_cache_valid():
            _LOGGER.debug("Using cached discovery results")
            return self._discovery_cache.copy()

        devices: list[dict[str, Any]] = []
        seen_devices: set[str] = set()

        try:
            responses = await self.send_broadcast_request(
                discover(),
                broadcast_addresses=broadcast_addresses,
            )

            for response in responses:
                if response.get("result"):
                    device_info = response["result"]
                    device_id = (
                        device_info.get("ip", "")
                        or device_info.get("ble_mac")
                        or device_info.get("wifi_mac")
                        or f"device_{int(asyncio.get_running_loop().time())}_{hash(str(device_info)) % 10000}"
                    )

                    if device_id in seen_devices:
                        _LOGGER.debug(
                            "Skip duplicate device: %s (IP: %s, BLE_MAC: %s, WiFi_MAC: %s)",
                            device_id,
                            device_info.get("ip"),
                            device_info.get("ble_mac"),
                            device_info.get("wifi_mac"),
                        )
                        continue

                    seen_devices.add(device_id)
                    _LOGGER.debug(
                        "Add device: %s (IP: %s, BLE_MAC: %s, WiFi_MAC: %s)",
                        device_id,
                        device_info.get("ip"),
                        device_info.get("ble_mac"),
                        device_info.get("wifi_mac"),
                    )

                    device = {
                        "id": device_info.get("id", 0),
                        "device_type": device_info.get("device", "Unknown"),
                        "version": device_info.get("ver", 0),
                        "wifi_name": device_info.get("wifi_name", ""),
                        "ip": device_info.get("ip", ""),
                        "wifi_mac": device_info.get("wifi_mac", ""),
                        "ble_mac": device_info.get("ble_mac", ""),
                        "mac": device_info.get("wifi_mac")
                        or device_info.get("ble_mac", ""),
                        "model": device_info.get("device", "Unknown"),
                        "firmware": str(device_info.get("ver", 0)),
                    }

                    devices.append(device)
                    _LOGGER.info(
                        "Discovered device: Type=%s, Version=%s, WiFi=%s, IP=%s, MAC=%s",
                        device["device_type"],
                        device["version"],
                        device["wifi_name"],
                        device["ip"],
                        device["mac"],
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
        previous_data: dict[str, Any] | None = None,
        timeout: float = 2.5,
        delay_ms: int = 2000,
    ) -> dict[str, Any]:
        """Fetch device status and PV data."""
        current_data = previous_data or {}
        result_data = {
            "battery_soc": current_data.get("battery_soc", 0),
            "battery_power": current_data.get("battery_power", 0),
            "device_mode": current_data.get("device_mode", "Unknown"),
            "battery_status": current_data.get("battery_status", "Unknown"),
            "device_ip": device_ip,
            "last_update": asyncio.get_running_loop().time(),
            "pv1_power": current_data.get("pv1_power", 0),
            "pv1_voltage": current_data.get("pv1_voltage", 0),
            "pv1_current": current_data.get("pv1_current", 0),
            "pv1_state": current_data.get("pv1_state", 0),
            "pv2_power": current_data.get("pv2_power", 0),
            "pv2_voltage": current_data.get("pv2_voltage", 0),
            "pv2_current": current_data.get("pv2_current", 0),
            "pv2_state": current_data.get("pv2_state", 0),
            "pv3_power": current_data.get("pv3_power", 0),
            "pv3_voltage": current_data.get("pv3_voltage", 0),
            "pv3_current": current_data.get("pv3_current", 0),
            "pv3_state": current_data.get("pv3_state", 0),
            "pv4_power": current_data.get("pv4_power", 0),
            "pv4_voltage": current_data.get("pv4_voltage", 0),
            "pv4_current": current_data.get("pv4_current", 0),
            "pv4_state": current_data.get("pv4_state", 0),
        }

        async def delay(ms: int) -> None:
            await asyncio.sleep(ms / 1000.0)

        async def es_status_request() -> bool:
            try:
                mode_as_status_result = await self.send_request(
                    get_es_mode(0),
                    device_ip,
                    timeout=timeout,
                )
                status_data = mode_as_status_result.get("result", {})

                battery_soc = status_data.get(
                    "bat_soc", result_data.get("battery_soc", 0)
                )
                result_data["battery_soc"] = battery_soc
                ongrid_power = status_data.get(
                    "ongrid_power", result_data.get("battery_power", 0)
                )
                result_data["battery_power"] = abs(ongrid_power)
                result_data["device_mode"] = status_data.get("mode", "Unknown")
                if ongrid_power > 0:
                    result_data["battery_status"] = "Selling"
                elif ongrid_power < 0:
                    result_data["battery_status"] = "Charging"
                else:
                    result_data["battery_status"] = "Idle"
            except (TimeoutError, OSError, ValueError):
                return False
            return True

        async def pv_status_request() -> bool:
            try:
                pv_status_result = await self.send_request(
                    get_pv_status(0),
                    device_ip,
                    timeout=timeout,
                )
                pv_data = pv_status_result.get("result", {})

                for key in (
                    "pv1_power",
                    "pv1_voltage",
                    "pv1_current",
                    "pv1_state",
                    "pv2_power",
                    "pv2_voltage",
                    "pv2_current",
                    "pv2_state",
                    "pv3_power",
                    "pv3_voltage",
                    "pv3_current",
                    "pv3_state",
                    "pv4_power",
                    "pv4_voltage",
                    "pv4_current",
                    "pv4_state",
                ):
                    result_data[key] = pv_data.get(key, result_data.get(key, 0))
            except (TimeoutError, OSError, ValueError):
                return False
            return True

        try:
            await es_status_request()
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.error("Device %s ES.GetMode request failed: %s", device_ip, err)

        await delay(delay_ms)

        try:
            await pv_status_request()
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.error("Device %s PV.GetStatus request failed: %s", device_ip, err)

        return result_data

    async def get_device_info(
        self, device_ip: str, *, timeout: float = 5.0
    ) -> dict[str, Any]:
        """Fetch device information from a specific host."""
        response = await self.send_request(discover(), device_ip, timeout=timeout)
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            raise TypeError("No device information returned")
        return result
