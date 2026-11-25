"""Cover entity for a ROLLER22G roller blind."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Roller22GConfigEntry
from .entity import Roller22GEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Roller22GConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the cover."""
    async_add_entities([RollerCover(entry)])


class RollerCover(Roller22GEntity, CoverEntity):
    """The blind. Position 100 is fully raised, as the motor reports it."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "cover")

    async def async_added_to_hass(self) -> None:
        """Follow state changes."""
        self.async_on_remove(self._data.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_cover_position(self) -> int | None:
        """Return the position; an estimate while a move is in progress."""
        position = self._data.estimated_position
        return None if position is None else round(position)

    @property
    def is_closed(self) -> bool | None:
        """Return whether the blind is fully lowered."""
        position = self._data.estimated_position
        return None if position is None else round(position) == 0

    @property
    def is_opening(self) -> bool:
        return self._data.opening

    @property
    def is_closing(self) -> bool:
        return self._data.closing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Tell an estimate from a value the motor reported."""
        return {
            "reported_position": self._data.position,
            "position_is_estimate": self._data.moving,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Raise the blind fully."""
        await self._data.async_move(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Lower the blind fully."""
        await self._data.async_move(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the blind to a position."""
        await self._data.async_move(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the blind."""
        await self._data.async_stop()
