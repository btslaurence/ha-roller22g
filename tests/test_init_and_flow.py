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


import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

import custom_components.roller22g as integration

COVER = "cover.roller_3da8"


def _record(position, command_sent=False, outcome="done", error=None):
    return {
        "address": ADDRESS,
        "outcome": outcome,
        "error": error,
        "position": position,
        "command_sent": command_sent,
        "steps": [],
    }


class FakeMotor:
    """Stands in for the exchange functions; moves instantly."""

    def __init__(self, position=100.0):
        self.position = position
        self.calls = []
        self.fail_command = False

    async def read(self, hass, address):
        self.calls.append(("read",))
        return _record(self.position)

    async def goto(self, hass, address, percent):
        self.calls.append(("goto", percent))
        if self.fail_command:
            return _record(None, outcome="connect_failed", error="nope")
        before = self.position
        self.position = float(percent)
        return _record(before, command_sent=True)

    async def stop(self, hass, address):
        self.calls.append(("stop",))
        return _record(self.position, command_sent=True)


@pytest.fixture
def motor(monkeypatch):
    fake = FakeMotor()
    monkeypatch.setattr(integration, "async_read", fake.read)
    monkeypatch.setattr(integration, "async_goto", fake.goto)
    monkeypatch.setattr(integration, "async_stop", fake.stop)
    monkeypatch.setattr(integration, "CHECK_MARGIN", 0.0)
    monkeypatch.setattr(integration, "SPEED_DOWN", 100000.0)
    monkeypatch.setattr(integration, "SPEED_UP", 100000.0)
    monkeypatch.setattr(integration, "RECHECK_MIN", 0.0)
    return fake


async def _setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="eb:c0:e9:11:3d:a8",
        title="Roller 3DA8",
        data={CONF_ADDRESS: ADDRESS},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_setup_reads_position_once_and_sends_no_command(hass, motor):
    await _setup(hass)

    assert motor.calls == [("read",)]
    state = hass.states.get(COVER)
    assert state.state == "open"
    assert state.attributes["current_position"] == 100
    assert state.attributes["position_is_estimate"] is False


async def test_set_position_sends_one_command_then_checks_once(hass, motor):
    entry = await _setup(hass)
    motor.calls.clear()

    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": COVER, "position": 80}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert motor.calls == [("goto", 80), ("read",)]
    state = hass.states.get(COVER)
    assert state.state == "open"
    assert state.attributes["current_position"] == 80
    assert state.attributes["reported_position"] == 80.0
    assert state.attributes["position_is_estimate"] is False
    assert entry.runtime_data.moving is False


async def test_open_and_close_map_to_100_and_0(hass, motor):
    await _setup(hass)
    motor.calls.clear()

    await hass.services.async_call("cover", "close_cover", {"entity_id": COVER}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(COVER).state == "closed"

    await hass.services.async_call("cover", "open_cover", {"entity_id": COVER}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(COVER).state == "open"
    assert [call for call in motor.calls if call[0] == "goto"] == [("goto", 0), ("goto", 100)]


async def test_moving_state_and_estimate_while_travelling(hass, motor, monkeypatch):
    monkeypatch.setattr(integration, "SPEED_DOWN", 1.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(integration, "_now", lambda: clock["now"])
    entry = await _setup(hass)

    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": COVER, "position": 80}, blocking=True
    )
    data = entry.runtime_data
    assert hass.states.get(COVER).state == "closing"
    assert data.estimated_position == 100.0

    clock["now"] += 5
    assert data.estimated_position == 95.0
    clock["now"] += 100
    assert data.estimated_position == 80.0

    # The check is still waiting out the travel time; stop cancels it.
    motor.position = 91.0
    await hass.services.async_call("cover", "stop_cover", {"entity_id": COVER}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert motor.calls[-1] == ("stop",)
    state = hass.states.get(COVER)
    assert state.state == "open"
    assert state.attributes["current_position"] == 91
    assert state.attributes["position_is_estimate"] is False
    assert data.moving is False


async def test_blind_that_stops_short_shows_its_real_position(hass, motor, monkeypatch):
    await _setup(hass)

    async def stuck_goto(hass_, address, percent):
        motor.calls.append(("goto", percent))
        motor.position = 88.0  # obstructed: never reaches the target
        return _record(100.0, command_sent=True)

    monkeypatch.setattr(integration, "async_goto", stuck_goto)
    motor.calls.clear()
    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": COVER, "position": 50}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    # One read sees 88, a second read sees 88 again and accepts it.
    assert motor.calls == [("goto", 50), ("read",), ("read",)]
    assert hass.states.get(COVER).attributes["current_position"] == 88


async def test_failed_command_raises_and_leaves_state_alone(hass, motor):
    entry = await _setup(hass)
    motor.fail_command = True

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover", "set_cover_position", {"entity_id": COVER, "position": 50}, blocking=True
        )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data.moving is False
    assert hass.states.get(COVER).attributes["current_position"] == 100
    assert hass.states.get("sensor.roller_3da8_last_exchange").state == "connect_failed"


async def test_entities_of_earlier_versions_are_removed(hass, motor):
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="eb:c0:e9:11:3d:a8",
        title="Roller 3DA8",
        data={CONF_ADDRESS: ADDRESS},
    )
    entry.add_to_hass(hass)
    old = registry.async_get_or_create(
        "number", DOMAIN, f"{ADDRESS}_target_position", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert registry.async_get(old.entity_id) is None
    assert hass.states.get(COVER) is not None


async def test_probe_button_runs_probe(hass, motor):
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="eb:c0:e9:11:3d:a8",
        title="Roller 3DA8",
        data={CONF_ADDRESS: ADDRESS},
    )
    entry.add_to_hass(hass)
    # Present (and enabled) from an earlier version, as on the real install.
    for domain, key in (("button", "probe"), ("sensor", "probe_result")):
        registry.async_get_or_create(domain, DOMAIN, f"{ADDRESS}_{key}", config_entry=entry)

    fake = {"address": ADDRESS, "verdict": "match_header_58", "error": None, "steps": []}
    with patch("custom_components.roller22g.async_run_probe", return_value=fake) as run:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        run.assert_not_called()
        button = registry.async_get_entity_id("button", DOMAIN, f"{ADDRESS}_probe")
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
        run.assert_called_once_with(hass, ADDRESS)

    sensor = registry.async_get_entity_id("sensor", DOMAIN, f"{ADDRESS}_probe_result")
    assert hass.states.get(sensor).state == "match_header_58"
    assert await hass.config_entries.async_unload(entry.entry_id)
