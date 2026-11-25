"""ROLLER22G roller blind motors over Bluetooth LE.

This version is for verifying the protocol against a real motor: a read-only
probe, plus stop and go-to-position with position feedback.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_ADDRESS,
    DEFAULT_HEADER,
    OUTCOME_RUNNING,
    VERDICT_ERROR,
    VERDICT_RUNNING,
)
from .exchange import async_goto, async_stop
from .probe import async_run_probe

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


@dataclass
class Roller22GData:
    """Runtime state for one motor."""

    hass: HomeAssistant
    address: str
    verdict: str | None = None
    result: dict[str, Any] | None = None
    header: str = DEFAULT_HEADER
    position: float | None = None
    outcome: str | None = None
    exchange: dict[str, Any] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    _listeners: list[Callable[[], None]] = field(default_factory=list)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for result changes; returns the remover."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_probe(self) -> None:
        """Run the read-only probe; a second request while one runs is dropped."""
        if self._lock.locked():
            return
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


    async def async_stop(self) -> None:
        """Stop the motor.

        If a move is being watched, the stop goes out on that connection;
        otherwise a new connection is opened for it.
        """
        if self._lock.locked():
            self._stop_event.set()
            return
        await self._async_exchange(async_stop(self.hass, self.address, self.header))

    async def async_goto(self, percent: int) -> None:
        """Move to a position with the selected header."""
        self._stop_event.clear()
        await self._async_exchange(
            async_goto(self.hass, self.address, self.header, percent, self._stop_event)
        )

    @callback
    def async_start_goto(self, percent: int) -> None:
        """Start a move in the background so the caller is not held up."""

        async def _run() -> None:
            try:
                await self.async_goto(percent)
            except Exception:
                _LOGGER.exception("%s: move to %s failed", self.address, percent)

        self.hass.async_create_background_task(_run(), f"roller22g move {self.address}")

    async def _async_exchange(self, coro: Any) -> None:
        if self._lock.locked():
            coro.close()
            return
        async with self._lock:
            self.outcome = OUTCOME_RUNNING
            self._notify()
            try:
                self.exchange = await coro
                self.outcome = self.exchange["outcome"]
                if self.exchange["position_after"] is not None:
                    self.position = self.exchange["position_after"]
                elif self.exchange["position_before"] is not None:
                    self.position = self.exchange["position_before"]
            except Exception as err:
                self.exchange = {
                    "address": self.address,
                    "outcome": VERDICT_ERROR,
                    "error": f"{type(err).__name__}: {err}",
                    "steps": [],
                }
                self.outcome = VERDICT_ERROR
                raise
            finally:
                self._notify()


type Roller22GConfigEntry = ConfigEntry[Roller22GData]


async def async_setup_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Set up one motor. Nothing is sent to the motor here."""
    entry.runtime_data = Roller22GData(hass, entry.data[CONF_ADDRESS])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
