"""Select for the command header used by motion frames."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Roller22GConfigEntry
from .const import HEADERS
from .entity import Roller22GEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Roller22GConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the header select."""
    async_add_entities([CommandHeader(entry)])


class CommandHeader(Roller22GEntity, SelectEntity):
    """Which of the two documented command headers to use. Not persisted."""

    _attr_options = list(HEADERS)
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "command_header")

    @property
    def current_option(self) -> str:
        """Return the selected header."""
        return self._data.header

    async def async_select_option(self, option: str) -> None:
        """Select a header."""
        self._data.header = option
        self.async_write_ha_state()
