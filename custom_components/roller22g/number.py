"""Number that moves the blind to a position."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Roller22GConfigEntry
from .entity import Roller22GEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Roller22GConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the position number."""
    async_add_entities([TargetPosition(entry)])


class TargetPosition(Roller22GEntity, NumberEntity):
    """Position of the blind; 100 is fully raised. Setting it moves the blind."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "target_position")

    async def async_added_to_hass(self) -> None:
        """Follow position changes."""
        self.async_on_remove(self._data.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the last position the motor reported."""
        return self._data.position

    async def async_set_native_value(self, value: float) -> None:
        """Start moving the blind; the result shows up on the result sensor."""
        self._data.async_start_goto(round(value))
