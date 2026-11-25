"""Stop and go-to-position exchanges with position feedback.

Each exchange opens its own connection, records every frame written and every
notification received, and closes the connection again.

A move is only started when the motor has reported where it is. While it
moves, the position is polled; if it leaves the span between the starting
position and the target, a stop frame is sent.
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
    HEADERS,
    OUTCOME_DONE,
    OUTCOME_GUARD_STOP,
    OUTCOME_NO_STATUS,
    RESPONSE_UUID,
    VERDICT_CONNECT_FAILED,
    VERDICT_ERROR,
    VERDICT_NOT_FOUND,
)
from .protocol import build_goto, build_read_status, build_stop, parse_response

_LOGGER = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3
SETTLE_SECONDS = 1.0
REPLY_WAIT_SECONDS = 1.0
MOVE_TIMEOUT_SECONDS = 40.0
MIN_OBSERVE_SECONDS = 5.0
STABLE_READS = 3
GUARD_MARGIN = 5.0


class _Link:
    """One connection with a frame log."""

    def __init__(self, client: BleakClientWithServiceCache, address: str) -> None:
        self.client = client
        self.address = address
        self.records: list[dict[str, Any]] = []
        self._rx: list[tuple[float, bytes]] = []

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._rx.append((time.monotonic(), bytes(data)))
        _LOGGER.info("%s: rx %s", self.address, bytes(data).hex(" "))

    async def start(self) -> None:
        await self.client.start_notify(RESPONSE_UUID, self._on_notify)
        await asyncio.sleep(SETTLE_SECONDS)

    async def send(self, name: str, frame: bytes, wait: float) -> dict[str, Any]:
        """Write one frame, wait, and record what came back."""
        sent_at = time.monotonic()
        write_error: str | None = None
        _LOGGER.info("%s: tx %s (%s)", self.address, frame.hex(" "), name)
        try:
            await self.client.write_gatt_char(COMMAND_UUID, frame, response=True)
        except (BleakError, TimeoutError) as err:
            write_error = f"{type(err).__name__}: {err}"
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
        record = {"step": name, "tx": frame.hex(" "), "write_error": write_error, "rx": rx}
        self.records.append(record)
        return record


def position_of(record: dict[str, Any]) -> float | None:
    """Return the last position (percent) reported in a record, if any."""
    for rx in reversed(record["rx"]):
        parsed = rx["parsed"]
        if parsed.get("kind") != "status":
            continue
        if "position_raw" in parsed:
            return parsed["position_raw"] / 10
        return float(parsed["position_percent"])
    return None


def _outside(position: float, start: float, target: float) -> bool:
    low, high = sorted((start, target))
    return position < low - GUARD_MARGIN or position > high + GUARD_MARGIN


async def _async_run(
    hass: HomeAssistant, address: str, header_key: str, action: str, target: int | None
) -> dict[str, Any]:
    header = HEADERS[header_key]
    result: dict[str, Any] = {
        "address": address,
        "action": action,
        "header": header_key,
        "target": target,
        "started": dt_util.now().isoformat(timespec="seconds"),
        "outcome": VERDICT_ERROR,
        "error": None,
        "position_before": None,
        "position_after": None,
        "guard_tripped": False,
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
    status = build_read_status(header)
    try:
        await link.start()
        before = position_of(await link.send("status_before", status, REPLY_WAIT_SECONDS))
        result["position_before"] = before

        if action == "stop":
            await link.send("stop", build_stop(header), REPLY_WAIT_SECONDS)
            after = position_of(await link.send("status_after", status, REPLY_WAIT_SECONDS))
            result["position_after"] = after
            result["outcome"] = OUTCOME_DONE if after is not None else OUTCOME_NO_STATUS
            return result

        assert target is not None
        if before is None:
            # No feedback, no move: the guard below needs a starting position.
            result["outcome"] = OUTCOME_NO_STATUS
            result["error"] = "motor did not report its position; move not sent"
            return result

        await link.send("goto", build_goto(header, target), REPLY_WAIT_SECONDS)
        started = time.monotonic()
        last: float | None = None
        stable = 0
        while time.monotonic() - started < MOVE_TIMEOUT_SECONDS:
            if not client.is_connected:
                result["error"] = "link dropped while watching the move"
                break
            position = position_of(await link.send("status_poll", status, REPLY_WAIT_SECONDS))
            if position is None:
                continue
            if _outside(position, before, target):
                await link.send("guard_stop", build_stop(header), REPLY_WAIT_SECONDS)
                result["guard_tripped"] = True
                after_guard = position_of(
                    await link.send("status_after_guard", status, REPLY_WAIT_SECONDS)
                )
                last = position if after_guard is None else after_guard
                break
            stable = stable + 1 if position == last else 0
            last = position
            if stable >= STABLE_READS - 1 and time.monotonic() - started >= MIN_OBSERVE_SECONDS:
                break

        result["position_after"] = last
        if result["guard_tripped"]:
            result["outcome"] = OUTCOME_GUARD_STOP
        elif last is None:
            result["outcome"] = OUTCOME_NO_STATUS
        else:
            result["outcome"] = OUTCOME_DONE
        return result
    except (BleakError, TimeoutError) as err:
        result["error"] = f"{type(err).__name__}: {err}"
        return result
    finally:
        try:
            if client.is_connected:
                await client.stop_notify(RESPONSE_UUID)
            await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: closing the link failed: %s", address, err)
        result["finished"] = dt_util.now().isoformat(timespec="seconds")
        _LOGGER.info("%s: %s outcome %s", address, action, result["outcome"])


async def async_stop(hass: HomeAssistant, address: str, header_key: str) -> dict[str, Any]:
    """Send stop and read the position."""
    return await _async_run(hass, address, header_key, "stop", None)


async def async_goto(
    hass: HomeAssistant, address: str, header_key: str, percent: int
) -> dict[str, Any]:
    """Move to a position (100 = fully raised) and watch it get there."""
    return await _async_run(hass, address, header_key, "goto", percent)
