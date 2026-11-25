"""Diagnostic buttons: read the position, run the read-only probe."""

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
    async_add_entities([RefreshButton(entry), ProbeButton(entry)])


class RefreshButton(Roller22GEntity, ButtonEntity):
    """Reads the position from the motor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "refresh")

    async def async_press(self) -> None:
        """Read the position."""
        await self._data.async_refresh()


class ProbeButton(Roller22GEntity, ButtonEntity):
    """Runs login and read status against the motor. Does not move it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "probe")

    async def async_press(self) -> None:
        """Run the probe."""
        await self._data.async_probe()
