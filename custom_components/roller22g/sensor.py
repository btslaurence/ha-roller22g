"""Sensors that report the last probe and the last motion exchange."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Roller22GConfigEntry
from .const import OUTCOME_NOT_RUN, OUTCOMES, VERDICT_NOT_RUN, VERDICTS
from .entity import Roller22GEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Roller22GConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        [BatterySensor(entry), ProbeResultSensor(entry), LastExchangeSensor(entry)]
    )


class BatterySensor(Roller22GEntity, SensorEntity):
    """Battery state as the motor reports it in its status frame.

    The motor reports one of three states, not a charge level.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normal", "low", "none", "unknown"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "battery")

    async def async_added_to_hass(self) -> None:
        """Follow state changes."""
        self.async_on_remove(self._data.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        """Return the battery state from the last status frame."""
        return self._data.battery

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the undocumented trailing bytes of the status frame."""
        return {"status_extra": self._data.status_extra}


class ProbeResultSensor(Roller22GEntity, SensorEntity):
    """Verdict of the last probe, with the recorded frames as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = VERDICTS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    # The frame log is for one-off inspection, not for history.
    _unrecorded_attributes = frozenset({"steps", "characteristics"})

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "probe_result")

    async def async_added_to_hass(self) -> None:
        """Follow result changes."""
        self.async_on_remove(self._data.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return the verdict."""
        return self._data.verdict or VERDICT_NOT_RUN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full probe record."""
        result = self._data.result
        if result is None:
            return None
        return {key: value for key, value in result.items() if key != "verdict"}


class LastExchangeSensor(Roller22GEntity, SensorEntity):
    """Outcome of the last Bluetooth exchange, with its frames as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = OUTCOMES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"steps"})

    def __init__(self, entry: Roller22GConfigEntry) -> None:
        super().__init__(entry, "last_exchange")

    async def async_added_to_hass(self) -> None:
        """Follow result changes."""
        self.async_on_remove(self._data.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return the outcome."""
        return self._data.outcome or OUTCOME_NOT_RUN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full exchange record."""
        exchange = self._data.exchange
        if exchange is None:
            return None
        return {key: value for key, value in exchange.items() if key != "outcome"}
