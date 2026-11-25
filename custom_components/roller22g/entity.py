"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import Roller22GConfigEntry, Roller22GData
from .const import DOMAIN, LOCAL_NAME


class Roller22GEntity(Entity):
    """Base for entities that belong to one motor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: Roller22GConfigEntry, key: str) -> None:
        self._data: Roller22GData = entry.runtime_data
        address = self._data.address
        self._attr_unique_id = f"{address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name=entry.title,
            model=LOCAL_NAME,
        )
