"""Config flow for ROLLER22G motors."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_ADDRESS, DOMAIN, LOCAL_NAME

_MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _normalize(value: str) -> str | None:
    """Return the address in upper-case colon form, or None if malformed."""
    address = value.strip().upper().replace("-", ":")
    return address if _MAC.match(address) else None


def _title(address: str) -> str:
    return f"Roller {address.replace(':', '')[-4:]}"


class Roller22GConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one motor by Bluetooth address."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a motor found by the Bluetooth integration."""
        address = discovery_info.address.upper()
        await self.async_set_unique_id(format_mac(address))
        self._abort_if_unique_id_configured()
        self._discovered = address
        self.context["title_placeholders"] = {"name": _title(address)}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered motor."""
        assert self._discovered is not None
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._discovered),
                data={CONF_ADDRESS: self._discovered},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"address": self._discovered},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a motor by typing its address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = _normalize(user_input[CONF_ADDRESS])
            if address is None:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                # A discovery flow for the same motor may be waiting; adding
                # the motor by hand takes precedence over it.
                await self.async_set_unique_id(
                    format_mac(address), raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_title(address), data={CONF_ADDRESS: address}
                )

        configured = self._async_current_ids(include_ignore=False)
        seen = sorted(
            info.address.upper()
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.name == LOCAL_NAME
            and format_mac(info.address) not in configured
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS, default=seen[0] if seen else ""): str}
            ),
            description_placeholders={"seen": ", ".join(seen) or "-"},
            errors=errors,
        )
