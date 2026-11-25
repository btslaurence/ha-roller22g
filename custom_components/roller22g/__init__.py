"""ROLLER22G roller blind motors over Bluetooth LE."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ADDRESS,
    OUTCOME_DONE,
    OUTCOME_RUNNING,
    SPEED_DOWN,
    SPEED_UP,
    VERDICT_ERROR,
    VERDICT_RUNNING,
)
from .exchange import async_goto, async_read, async_stop
from .probe import async_run_probe

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.COVER, Platform.SENSOR]

# Entities of earlier versions that no longer exist.
_REMOVED = (
    ("number", "target_position"),
    ("select", "command_header"),
    ("button", "stop"),
)

# Seconds added to the estimated travel time before the position is checked.
CHECK_MARGIN = 3.0
# A position this close to the target counts as arrived.
ARRIVED_TOLERANCE = 1.0
RECHECK_MIN = 3.0
RECHECK_MAX = 60.0
MAX_CHECKS = 6
TICK = timedelta(seconds=1)
INITIAL_READ_ATTEMPTS = 4
INITIAL_READ_RETRY = 15.0


def _now() -> float:
    return time.monotonic()


def travel_seconds(start: float, target: float) -> float:
    """Estimated time to travel between two positions (100 = raised)."""
    speed = SPEED_UP if target > start else SPEED_DOWN
    return abs(target - start) / speed


class Roller22GData:
    """State and operations for one motor."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address
        # Probe.
        self.verdict: str | None = None
        self.result: dict[str, Any] | None = None
        # Last Bluetooth exchange.
        self.outcome: str | None = None
        self.exchange: dict[str, Any] | None = None
        # Position: the last value the motor reported, and the move in progress.
        self.position: float | None = None
        self.battery: str | None = None
        self.status_extra: str | None = None
        self.target: float | None = None
        self._move_from: float | None = None
        self._move_started: float | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._check_task: asyncio.Task[None] | None = None
        self._untick: CALLBACK_TYPE | None = None

    # Listeners -----------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for state changes; returns the remover."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self, *_: Any) -> None:
        for listener in list(self._listeners):
            listener()

    # Derived state -------------------------------------------------------

    @property
    def moving(self) -> bool:
        """Whether a move is believed to be in progress."""
        return self.target is not None

    @property
    def opening(self) -> bool:
        return self.moving and self._move_from is not None and self.target > self._move_from

    @property
    def closing(self) -> bool:
        return self.moving and self._move_from is not None and self.target < self._move_from

    @property
    def estimated_position(self) -> float | None:
        """Reported position, or an estimate by elapsed time while moving."""
        if not self.moving or self._move_from is None or self._move_started is None:
            return self.position
        total = travel_seconds(self._move_from, self.target)
        if total <= 0:
            return self.target
        fraction = min(1.0, (_now() - self._move_started) / total)
        return self._move_from + (self.target - self._move_from) * fraction

    # Operations ----------------------------------------------------------

    async def _async_exchange(self, coro: Any) -> dict[str, Any]:
        """Run one exchange under the lock and publish its record."""
        async with self._lock:
            self.outcome = OUTCOME_RUNNING
            self._notify()
            try:
                self.exchange = await coro
            except Exception as err:
                self.exchange = {
                    "address": self.address,
                    "outcome": VERDICT_ERROR,
                    "error": f"{type(err).__name__}: {err}",
                    "position": None,
                    "command_sent": False,
                    "steps": [],
                }
                raise
            finally:
                self.outcome = self.exchange["outcome"] if self.exchange else VERDICT_ERROR
                if self.exchange and self.exchange.get("battery") is not None:
                    self.battery = self.exchange["battery"]
                    self.status_extra = self.exchange.get("status_extra")
                self._notify()
            return self.exchange

    @callback
    def _end_move(self) -> None:
        self.target = None
        self._move_from = None
        self._move_started = None
        if self._untick is not None:
            self._untick()
            self._untick = None

    @callback
    def _cancel_check(self) -> None:
        if self._check_task is not None and not self._check_task.done():
            self._check_task.cancel()
        self._check_task = None

    async def async_refresh(self) -> None:
        """Read the position from the motor."""
        record = await self._async_exchange(async_read(self.hass, self.address))
        if record["position"] is not None:
            self.position = record["position"]
            self._notify()

    async def async_move(self, target: int) -> None:
        """Send the motor to a position; it stops there by itself."""
        self._cancel_check()
        record = await self._async_exchange(async_goto(self.hass, self.address, target))
        if record["position"] is not None:
            self.position = record["position"]
        if not record["command_sent"]:
            self._end_move()
            self._notify()
            raise HomeAssistantError(
                f"Could not send the move to {self.address}: "
                f"{record['error'] or record['outcome']}"
            )

        start = self.position if self.position is not None else float(target)
        self.target = float(target)
        self._move_from = start
        self._move_started = _now()
        if self._untick is None:
            self._untick = async_track_time_interval(self.hass, self._notify, TICK)
        self._notify()
        self._check_task = self.hass.async_create_background_task(
            self._async_check(float(target), travel_seconds(start, target) + CHECK_MARGIN),
            f"roller22g check {self.address}",
        )

    async def _async_check(self, target: float, delay: float) -> None:
        """After the estimated travel time, read where the motor is."""
        previous: float | None = None
        try:
            for _ in range(MAX_CHECKS):
                await asyncio.sleep(delay)
                record = await self._async_exchange(async_read(self.hass, self.address))
                position = record["position"]
                if position is None:
                    delay = RECHECK_MIN
                    continue
                self.position = position
                if abs(position - target) <= ARRIVED_TOLERANCE or position == previous:
                    # Arrived, or stopped short of the target.
                    break
                previous = position
                delay = min(
                    RECHECK_MAX, max(RECHECK_MIN, travel_seconds(position, target) + 1.0)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("%s: checking the position failed", self.address)
        self._check_task = None
        self._end_move()
        self._notify()

    async def async_stop(self) -> None:
        """Stop the motor and read where it ended up."""
        self._cancel_check()
        record = await self._async_exchange(async_stop(self.hass, self.address))
        if record["command_sent"]:
            self._end_move()
        if record["position"] is not None:
            self.position = record["position"]
        self._notify()
        if not record["command_sent"]:
            raise HomeAssistantError(
                f"Could not send stop to {self.address}: "
                f"{record['error'] or record['outcome']}"
            )

    async def async_probe(self) -> None:
        """Run the read-only probe."""
        async with self._lock:
            self.verdict = VERDICT_RUNNING
            self._notify()
            try:
                self.result = await async_run_probe(self.hass, self.address)
                self.verdict = self.result["verdict"]
            except Exception as err:
                self.result = {
                    "address": self.address,
                    "verdict": VERDICT_ERROR,
                    "error": f"{type(err).__name__}: {err}",
                    "steps": [],
                }
                self.verdict = VERDICT_ERROR
                raise
            finally:
                self._notify()

    @callback
    def async_shutdown(self) -> None:
        """Cancel timers and tasks."""
        self._cancel_check()
        self._end_move()


type Roller22GConfigEntry = ConfigEntry[Roller22GData]


async def async_setup_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Set up one motor."""
    data = Roller22GData(hass, entry.data[CONF_ADDRESS])
    entry.runtime_data = data

    registry = er.async_get(hass)
    for domain, key in _REMOVED:
        entity_id = registry.async_get_entity_id(domain, entry.domain, f"{data.address}_{key}")
        if entity_id is not None:
            registry.async_remove(entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _initial_read() -> None:
        # Right after a restart the motor may not have been heard yet.
        for _ in range(INITIAL_READ_ATTEMPTS):
            try:
                await data.async_refresh()
            except Exception:
                _LOGGER.exception("%s: initial position read failed", data.address)
                return
            if data.position is not None:
                return
            await asyncio.sleep(INITIAL_READ_RETRY)

    # One status read so the cover has a position; it does not move the motor.
    entry.async_create_background_task(hass, _initial_read(), "roller22g initial read")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Unload a config entry."""
    entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
