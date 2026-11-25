"""Config flow and entity wiring."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.roller22g.const import CONF_ADDRESS, DOMAIN

ADDRESS = "EB:C0:E9:11:3D:A8"


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "eb-c0-e9-11-3d-a8"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["title"] == "Roller 3DA8"


async def test_user_flow_rejects_bad_address(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "not-a-mac"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_duplicate_is_aborted(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id="eb:c0:e9:11:3d:a8", data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.ABORT


async def test_setup_sends_nothing_and_button_runs_probe(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="eb:c0:e9:11:3d:a8",
        title="Roller 3DA8",
        data={CONF_ADDRESS: ADDRESS},
    )
    entry.add_to_hass(hass)

    fake = {"address": ADDRESS, "verdict": "match_header_58", "error": None, "steps": [{"step": "login"}]}
    with patch(
        "custom_components.roller22g.async_run_probe", return_value=fake
    ) as run:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        run.assert_not_called()

        sensor = hass.states.get("sensor.roller_3da8_probe_result")
        assert sensor is not None and sensor.state == "not_run"

        await hass.services.async_call(
            "button", "press", {"entity_id": "button.roller_3da8_run_read_only_probe"}, blocking=True
        )
        run.assert_called_once_with(hass, ADDRESS)

    sensor = hass.states.get("sensor.roller_3da8_probe_result")
    assert sensor.state == "match_header_58"
    assert sensor.attributes["steps"] == [{"step": "login"}]
    assert "verdict" not in sensor.attributes

    assert await hass.config_entries.async_unload(entry.entry_id)
