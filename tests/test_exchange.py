"""Short exchanges against a simulated motor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bleak.exc import BleakError

from custom_components.roller22g import exchange
from custom_components.roller22g.const import COMMAND_UUID, RESPONSE_UUID
from custom_components.roller22g.protocol import (
    HEADER_58,
    build_goto,
    build_read_status,
    build_stop,
)

ADDRESS = "EB:C0:E9:11:3D:A8"
STATUS = build_read_status(HEADER_58)


class Motor:
    """Answers status with its position; records what it is told to do."""

    def __init__(self, raw=1000, silent=False, drop_after=None):
        self.raw = raw
        self.silent = silent
        self.drop_after = drop_after
        self.is_connected = True
        self.written = []
        self._notify = None
        self.services = SimpleNamespace(get_service=lambda uuid: object())

    async def start_notify(self, uuid, cb):
        assert uuid == RESPONSE_UUID
        self._notify = cb

    async def stop_notify(self, uuid):
        self._notify = None

    async def disconnect(self):
        self.is_connected = False

    async def write_gatt_char(self, uuid, data, response=False):
        assert uuid == COMMAND_UUID and response is True
        if self.drop_after is not None and len(self.written) >= self.drop_after:
            self.is_connected = False
            raise BleakError("gone")
        frame = bytes(data)
        self.written.append(frame)
        if frame[4] == 0xD1 and not self.silent:
            self._notify(
                None,
                bytearray(
                    bytes.fromhex("ff010203d102")
                    + bytes((self.raw // 10,))
                    + self.raw.to_bytes(2, "little")
                    + bytes.fromhex("9e4e")
                ),
            )


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for name in ("SETTLE_SECONDS", "AFTER_WRITE_SECONDS", "AFTER_STOP_SECONDS"):
        monkeypatch.setattr(exchange, name, 0)
    monkeypatch.setattr(exchange, "STATUS_TIMEOUT_SECONDS", 0.05)


async def _run(hass, motor, call):
    async def _connect(*args, **kwargs):
        return motor

    with (
        patch.object(exchange.bluetooth, "async_ble_device_from_address", return_value=object()),
        patch.object(exchange, "establish_connection", side_effect=_connect),
    ):
        return await call(hass)


async def test_goto_is_one_status_read_and_one_command(hass):
    motor = Motor(raw=1000)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, 80))

    assert motor.written == [STATUS, build_goto(HEADER_58, 80)]
    assert build_goto(HEADER_58, 80) == bytes.fromhex("ff58ea41bf032003")
    assert result["outcome"] == "done"
    assert result["command_sent"] is True
    assert result["position"] == 100.0
    assert [step["step"] for step in result["steps"]] == ["status", "goto"]
    assert all("at" in step for step in result["steps"])
    assert not motor.is_connected


async def test_goto_is_still_sent_when_status_does_not_come(hass):
    motor = Motor(silent=True)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, 0))

    assert motor.written == [STATUS, build_goto(HEADER_58, 0)]
    assert result["command_sent"] is True
    assert result["position"] is None


async def test_stop_goes_out_before_the_status_read(hass):
    motor = Motor(raw=934)
    result = await _run(hass, motor, lambda h: exchange.async_stop(h, ADDRESS))

    assert motor.written == [build_stop(HEADER_58), STATUS]
    assert result["command_sent"] is True
    assert result["position"] == 93.4


async def test_read_reports_position(hass):
    motor = Motor(raw=899)
    result = await _run(hass, motor, lambda h: exchange.async_read(h, ADDRESS))

    assert motor.written == [STATUS]
    assert result["outcome"] == "done"
    assert result["position"] == 89.9
    assert result["command_sent"] is False


async def test_read_without_reply(hass):
    result = await _run(hass, Motor(silent=True), lambda h: exchange.async_read(h, ADDRESS))
    assert result["outcome"] == "no_status"


async def test_nothing_is_written_after_the_link_drops(hass):
    motor = Motor(drop_after=1)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, 50))

    assert motor.written == [STATUS]
    assert result["command_sent"] is False
    assert result["outcome"] == "error"
    assert "gone" in result["error"]


async def test_device_not_seen(hass):
    with patch.object(exchange.bluetooth, "async_ble_device_from_address", return_value=None):
        result = await exchange.async_goto(hass, ADDRESS, 90)
    assert result["outcome"] == "device_not_found"
    assert result["command_sent"] is False


async def test_connect_failure(hass):
    with (
        patch.object(exchange.bluetooth, "async_ble_device_from_address", return_value=object()),
        patch.object(exchange, "establish_connection", side_effect=BleakError("nope")),
    ):
        result = await exchange.async_stop(hass, ADDRESS)
    assert result["outcome"] == "connect_failed"
    assert result["command_sent"] is False


def test_goto_rejects_out_of_range():
    with pytest.raises(ValueError):
        build_goto(HEADER_58, 101)
