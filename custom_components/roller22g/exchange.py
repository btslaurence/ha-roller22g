"""Short Bluetooth exchanges with a ROLLER22G motor.

Every exchange opens a connection, sends at most a few frames and closes the
connection again. A move is a single go-to-position frame: the motor travels
to the target and stops there by itself, so nothing here watches a move.
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
    OUTCOME_DONE,
    OUTCOME_NO_STATUS,
    RESPONSE_UUID,
    VERDICT_CONNECT_FAILED,
    VERDICT_ERROR,
    VERDICT_NOT_FOUND,
)
from .protocol import (
    HEADER_58,
    build_goto,
    build_read_status,
    build_stop,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

# Verified on a real motor: motion frames are obeyed with this header, no login.
HEADER = HEADER_58

CONNECT_ATTEMPTS = 3
SETTLE_SECONDS = 0.3
STATUS_TIMEOUT_SECONDS = 2.0
AFTER_WRITE_SECONDS = 0.1
AFTER_STOP_SECONDS = 1.0


class _Link:
    """One connection with a frame log."""

    def __init__(self, client: BleakClientWithServiceCache, address: str) -> None:
        self.client = client
        self.address = address
        self.records: list[dict[str, Any]] = []
        self._rx: list[tuple[float, bytes]] = []
        self._status = asyncio.Event()

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        self._rx.append((time.monotonic(), frame))
        if parse_response(frame).get("kind") == "status":
            self._status.set()
        _LOGGER.debug("%s: rx %s", self.address, frame.hex(" "))

    async def start(self) -> None:
        await self.client.start_notify(RESPONSE_UUID, self._on_notify)
        await asyncio.sleep(SETTLE_SECONDS)

    async def send(
        self, name: str, frame: bytes, wait: float, until_status: bool = False
    ) -> dict[str, Any]:
        """Write one frame and record what came back.

        With until_status the wait ends as soon as a status frame arrives.
        """
        if not self.client.is_connected:
            raise BleakError("link dropped")
        self._status.clear()
        sent_at = time.monotonic()
        clock = dt_util.now().strftime("%H:%M:%S.%f")[:-3]
        _LOGGER.debug("%s: tx %s (%s)", self.address, frame.hex(" "), name)
        await self.client.write_gatt_char(COMMAND_UUID, frame, response=True)
        if until_status:
            try:
                async with asyncio.timeout(wait):
                    await self._status.wait()
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(wait)
        rx = [
            {
                "after_ms": max(0, round((stamp - sent_at) * 1000)),
                "hex": data.hex(" "),
                "parsed": parse_response(data),
            }
            for stamp, data in self._rx
        ]
        self._rx.clear()
        record = {"step": name, "at": clock, "tx": frame.hex(" "), "rx": rx}
        self.records.append(record)
        return record


def status_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the last parsed status frame in a record, if any."""
    for rx in reversed(record["rx"]):
        if rx["parsed"].get("kind") == "status":
            return rx["parsed"]
    return None


def position_of(record: dict[str, Any]) -> float | None:
    """Return the last position (percent, 100 = fully raised) in a record."""
    parsed = status_of(record)
    if parsed is None:
        return None
    if "position_raw" in parsed:
        return parsed["position_raw"] / 10
    return float(parsed["position_percent"])


async def _async_run(
    hass: HomeAssistant, address: str, action: str, target: int | None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "address": address,
        "action": action,
        "target": target,
        "requested": dt_util.now().strftime("%H:%M:%S.%f")[:-3],
        "outcome": VERDICT_ERROR,
        "error": None,
        "position": None,
        "battery": None,
        "status_extra": None,
        "command_sent": False,
        "steps": [],
    }

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        result["outcome"] = VERDICT_NOT_FOUND
        result["error"] = "no connectable advertisement seen for this address"
        return result

    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            f"ROLLER22G {address}",
            max_attempts=CONNECT_ATTEMPTS,
        )
    except (BleakError, TimeoutError) as err:
        result["outcome"] = VERDICT_CONNECT_FAILED
        result["error"] = f"{type(err).__name__}: {err}"
        return result

    link = _Link(client, address)
    result["steps"] = link.records
    status = build_read_status(HEADER)
    try:
        await link.start()
        if action == "stop":
            # Stop first: every moment spent reading is travel.
            await link.send("stop", build_stop(HEADER), AFTER_STOP_SECONDS)
            result["command_sent"] = True
        record = await link.send("status", status, STATUS_TIMEOUT_SECONDS, until_status=True)
        result["position"] = position_of(record)
        parsed = status_of(record)
        if parsed is not None:
            result["battery"] = parsed["battery"]
            result["status_extra"] = parsed.get("extra")
        if action == "goto":
            assert target is not None
            await link.send("goto", build_goto(HEADER, target), AFTER_WRITE_SECONDS)
            result["command_sent"] = True
        if action == "read" and result["position"] is None:
            result["outcome"] = OUTCOME_NO_STATUS
        else:
            result["outcome"] = OUTCOME_DONE
    except (BleakError, TimeoutError) as err:
        result["error"] = f"{type(err).__name__}: {err}"
    finally:
        # Disconnecting ends the notification subscription with it, so there is
        # no separate unsubscribe: that would only keep the link up longer.
        result["closing"] = dt_util.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: closing the link failed: %s", address, err)
        result["finished"] = dt_util.now().strftime("%H:%M:%S.%f")[:-3]
        _LOGGER.info(
            "%s: %s%s -> %s, position %s",
            address,
            action,
            "" if target is None else f" {target}",
            result["outcome"],
            result["position"],
        )
    return result


async def async_read(hass: HomeAssistant, address: str) -> dict[str, Any]:
    """Read the position."""
    return await _async_run(hass, address, "read", None)


async def async_goto(hass: HomeAssistant, address: str, percent: int) -> dict[str, Any]:
    """Read the position, then send one go-to-position frame (100 = raised)."""
    return await _async_run(hass, address, "goto", percent)


async def async_stop(hass: HomeAssistant, address: str) -> dict[str, Any]:
    """Send stop, then read where the motor ended up."""
    return await _async_run(hass, address, "stop", None)
