"""Read-only probe of a ROLLER22G motor.

The probe connects, subscribes to notifications, and sends only two kinds of
frame: read status and login. It never sends a motion, limit, timer, clock or
password command. Every frame written and every notification received is
recorded so the result can be judged from Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    COMMAND_UUID,
    RESPONSE_UUID,
    SERVICE_UUID,
    VERDICT_CONNECT_FAILED,
    VERDICT_ERROR,
    VERDICT_LOGIN_ONLY,
    VERDICT_MATCH_58,
    VERDICT_MATCH_78,
    VERDICT_NO_RESPONSE,
    VERDICT_NOT_FOUND,
)
from .protocol import (
    HEADER_58,
    HEADER_78,
    build_login,
    build_read_status,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3
SETTLE_SECONDS = 1.5
STEP_WAIT_SECONDS = 2.5

# (step name, frame). Order matters: the two status requests are tried before
# login to learn whether the motor answers without it, then again after.
STEPS: list[tuple[str, bytes]] = [
    ("status_58_before_login", build_read_status(HEADER_58)),
    ("status_78_before_login", build_read_status(HEADER_78)),
    ("login", build_login()),
    ("status_58_after_login", build_read_status(HEADER_58)),
    ("status_78_after_login", build_read_status(HEADER_78)),
]
LOGIN_INDEX = 2


def _judge(steps: list[dict[str, Any]]) -> str:
    """Reduce the recorded steps to a verdict."""

    def got(step_names: tuple[str, ...], kind: str) -> bool:
        return any(
            rx["parsed"].get("kind") == kind
            for step in steps
            if step["step"] in step_names
            for rx in step["rx"]
        )

    if got(("status_58_before_login", "status_58_after_login"), "status"):
        return VERDICT_MATCH_58
    if got(("status_78_before_login", "status_78_after_login"), "status"):
        return VERDICT_MATCH_78
    if any(
        rx["parsed"].get("kind") == "login_result" and rx["parsed"].get("success")
        for step in steps
        for rx in step["rx"]
    ):
        return VERDICT_LOGIN_ONLY
    return VERDICT_NO_RESPONSE


async def async_run_probe(hass: HomeAssistant, address: str) -> dict[str, Any]:
    """Run the probe once and return a JSON-serializable result."""
    result: dict[str, Any] = {
        "address": address,
        "started": dt_util.now().isoformat(timespec="seconds"),
        "verdict": VERDICT_ERROR,
        "error": None,
        "rssi": None,
        "connections": 0,
        "service_found": None,
        "characteristics": {},
        "steps": [],
    }

    info = bluetooth.async_last_service_info(hass, address, connectable=True)
    if info is not None:
        result["rssi"] = info.rssi

    start_index = 0
    for session in range(2):
        ble_device = bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        )
        if ble_device is None:
            if session == 0:
                result["verdict"] = VERDICT_NOT_FOUND
                result["error"] = "no connectable advertisement seen for this address"
                return result
            break

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                f"ROLLER22G {address}",
                max_attempts=CONNECT_ATTEMPTS,
            )
        except (BleakError, TimeoutError) as err:
            if session == 0:
                result["verdict"] = VERDICT_CONNECT_FAILED
                result["error"] = f"{type(err).__name__}: {err}"
                return result
            break

        result["connections"] += 1
        try:
            start_index = await _async_session(client, result, start_index)
        except (BleakError, TimeoutError) as err:
            result["error"] = f"{type(err).__name__}: {err}"
            _LOGGER.info("%s: probe session ended early: %s", address, result["error"])
        finally:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError) as err:
                _LOGGER.debug("%s: disconnect failed: %s", address, err)

        if start_index >= len(STEPS):
            break
        # The link dropped part-way. Resume once, from login: a new connection
        # is a new session, and a motor that answers pre-login frames by
        # disconnecting still gets its login tested.
        start_index = LOGIN_INDEX

    result["verdict"] = _judge(result["steps"])
    result["finished"] = dt_util.now().isoformat(timespec="seconds")
    _LOGGER.info("%s: probe verdict %s", address, result["verdict"])
    return result


async def _async_session(
    client: BleakClientWithServiceCache, result: dict[str, Any], start_index: int
) -> int:
    """Run steps from start_index on one connection; return the next index."""
    address = result["address"]
    received: list[tuple[float, bytes]] = []

    def _on_notify(_sender: Any, data: bytearray) -> None:
        received.append((time.monotonic(), bytes(data)))
        _LOGGER.info("%s: rx %s", address, bytes(data).hex(" "))

    service = client.services.get_service(SERVICE_UUID)
    result["service_found"] = service is not None
    if service is None:
        result["error"] = "control service not present on this device"
        return len(STEPS)
    result["characteristics"] = {
        char.uuid: list(char.properties) for char in service.characteristics
    }

    await client.start_notify(RESPONSE_UUID, _on_notify)
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        if received:
            result["steps"].append(
                {
                    "step": "unsolicited_after_subscribe",
                    "tx": None,
                    "write_error": None,
                    "rx": _drain(received, time.monotonic() - SETTLE_SECONDS),
                }
            )

        index = start_index
        while index < len(STEPS):
            name, frame = STEPS[index]
            if not client.is_connected:
                result["steps"].append(
                    {
                        "step": name,
                        "tx": None,
                        "write_error": "link dropped before this step",
                        "rx": [],
                    }
                )
                return index
            sent_at = time.monotonic()
            write_error: str | None = None
            _LOGGER.info("%s: tx %s (%s)", address, frame.hex(" "), name)
            try:
                await client.write_gatt_char(COMMAND_UUID, frame, response=True)
            except (BleakError, TimeoutError) as err:
                write_error = f"{type(err).__name__}: {err}"
            await asyncio.sleep(STEP_WAIT_SECONDS)
            result["steps"].append(
                {
                    "step": name,
                    "tx": frame.hex(" "),
                    "write_error": write_error,
                    "rx": _drain(received, sent_at),
                }
            )
            index += 1
        return index
    finally:
        if client.is_connected:
            try:
                await client.stop_notify(RESPONSE_UUID)
            except (BleakError, TimeoutError) as err:
                _LOGGER.debug("%s: stop_notify failed: %s", address, err)


def _drain(received: list[tuple[float, bytes]], since: float) -> list[dict[str, Any]]:
    """Move collected notifications into a serializable list."""
    frames = [
        {
            "after_ms": max(0, round((stamp - since) * 1000)),
            "hex": data.hex(" "),
            "parsed": parse_response(data),
        }
        for stamp, data in received
    ]
    received.clear()
    return frames
