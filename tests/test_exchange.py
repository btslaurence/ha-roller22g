"""Short exchanges against a simulated motor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bleak.exc import BleakCharacteristicNotFoundError, BleakError

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

    def __init__(self, raw=1000, silent=False, drop_after=None, no_characteristic=False):
        self.raw = raw
        self.silent = silent
        self.drop_after = drop_after
        self.no_characteristic = no_characteristic
        self.is_connected = True
        self.written = []
        self.cache_cleared = False
        self._notify = None
        self.services = SimpleNamespace(get_service=lambda uuid: object())

    async def start_notify(self, uuid, cb):
        assert uuid == RESPONSE_UUID
        if self.no_characteristic:
            raise BleakCharacteristicNotFoundError(uuid)
        self._notify = cb

    async def clear_cache(self):
        self.cache_cleared = True
        return True

    async def stop_notify(self, uuid):
        raise AssertionError("the link is closed without a separate unsubscribe")

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
    monkeypatch.setattr(exchange, "RETRY_PAUSE_SECONDS", 0)
    monkeypatch.setattr(exchange, "STATUS_TIMEOUT_SECONDS", 0.05)


async def _run(hass, motor, call):
    """Run a call; a list of motors gives one motor per connection."""
    motors = list(motor) if isinstance(motor, list) else None

    async def _connect(*args, **kwargs):
        if motors is None:
            return motor
        return motors.pop(0)

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
    assert "closing" in result and "finished" in result
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
    assert result["battery"] == "normal"
    assert result["status_extra"] == "9e 4e"
    assert result["command_sent"] is False


async def test_read_without_reply(hass):
    result = await _run(hass, Motor(silent=True), lambda h: exchange.async_read(h, ADDRESS))
    assert result["outcome"] == "no_status"


async def test_nothing_is_written_after_the_link_drops(hass):
    motors = [Motor(drop_after=1) for _ in range(exchange.EXCHANGE_ATTEMPTS)]
    result = await _run(hass, list(motors), lambda h: exchange.async_goto(h, ADDRESS, 50))

    assert all(motor.written == [STATUS] for motor in motors)
    assert not any(motor.is_connected for motor in motors)
    assert result["command_sent"] is False
    assert result["outcome"] == "error"
    assert "gone" in result["error"]
    assert result["attempts"] == exchange.EXCHANGE_ATTEMPTS
    assert len(result["retried"]) == exchange.EXCHANGE_ATTEMPTS - 1


async def test_goto_goes_out_on_a_new_link_when_the_characteristic_is_missing(hass):
    broken, good = Motor(no_characteristic=True), Motor(raw=0)
    result = await _run(hass, [broken, good], lambda h: exchange.async_goto(h, ADDRESS, 100))

    assert broken.written == []
    assert broken.cache_cleared is True
    assert not broken.is_connected
    assert good.written == [STATUS, build_goto(HEADER_58, 100)]
    assert result["outcome"] == "done"
    assert result["error"] is None
    assert result["command_sent"] is True
    assert result["position"] == 0.0
    assert result["attempts"] == 2
    assert len(result["retried"]) == 1
    assert "BleakCharacteristicNotFoundError" in result["retried"][0]


async def test_stop_is_not_repeated_once_it_is_out(hass):
    # The link drops on the frame after the command: for stop that is the status read.
    first, spare = Motor(drop_after=1), Motor()
    result = await _run(hass, [first, spare], lambda h: exchange.async_stop(h, ADDRESS))

    assert first.written == [build_stop(HEADER_58)]
    assert spare.written == []
    assert result["command_sent"] is True
    assert result["attempts"] == 1
    assert result["retried"] == []
    assert result["outcome"] == "error"


async def test_first_try_records_one_attempt(hass):
    result = await _run(hass, Motor(), lambda h: exchange.async_read(h, ADDRESS))
    assert result["attempts"] == 1
    assert result["retried"] == []


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
