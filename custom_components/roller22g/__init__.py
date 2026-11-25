"""ROLLER22G roller blind motors over Bluetooth LE.

This version only verifies the protocol: it can run a read-only probe against
a motor and expose what the motor answered. It cannot move a blind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import CONF_ADDRESS, VERDICT_ERROR, VERDICT_RUNNING
from .probe import async_run_probe

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


@dataclass
class Roller22GData:
    """Runtime state for one motor."""

    hass: HomeAssistant
    address: str
    verdict: str | None = None
    result: dict[str, Any] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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


type Roller22GConfigEntry = ConfigEntry[Roller22GData]


async def async_setup_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Set up one motor. Nothing is sent to the motor here."""
    entry.runtime_data = Roller22GData(hass, entry.data[CONF_ADDRESS])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Roller22GConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
