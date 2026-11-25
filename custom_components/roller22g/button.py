"""Buttons: read-only probe and stop."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Roller22GConfigEntry
from .entity import Roller22GEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Roller22GConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_entities([ProbeButton(entry), StopButton(entry)])


class ProbeButton(Roller22GEntity, ButtonEntity):
    """Runs login and read status against the motor. Does not move it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "probe")

    async def async_press(self) -> None:
        """Run the probe."""
        await self._data.async_probe()


class StopButton(Roller22GEntity, ButtonEntity):
    """Sends the stop frame with the selected header."""

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "stop")

    async def async_press(self) -> None:
        """Send stop."""
        await self._data.async_stop()
