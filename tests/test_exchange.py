"""Stop and go-to-position against a simulated motor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.roller22g import exchange
from custom_components.roller22g.const import COMMAND_UUID, RESPONSE_UUID
from custom_components.roller22g.protocol import (
    HEADER_58,
    HEADER_78,
    build_goto,
    build_read_status,
    build_stop,
)

ADDRESS = "EB:C0:E9:11:3D:A8"


class Motor:
    """A motor that moves 40 raw units toward its target on every status read.

    accepts: the command header the motor obeys for motion frames (status is
    answered for either header, as the real motor does). inverted: the motor
    treats a go-to value as distance from the top instead of the reported scale.
    """

    def __init__(self, raw=1000, accepts=HEADER_58, inverted=False, silent=False, on_read=None):
        self.on_read = on_read
        self.reads = 0
        self.raw = raw
        self.target = raw
        self.accepts = accepts
        self.inverted = inverted
        self.silent = silent
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
        frame = bytes(data)
        self.written.append(frame)
        header, opcode = frame[:4], frame[4]
        if opcode == 0xD1:
            self.reads += 1
            if self.on_read is not None:
                self.on_read(self.reads)
            step = max(-40, min(40, self.target - self.raw))
            self.raw += step
            if not self.silent:
                self._notify(
                    None,
                    bytearray(
                        bytes.fromhex("ff010203d102")
                        + bytes((self.raw // 10,))
                        + self.raw.to_bytes(2, "little")
                        + bytes.fromhex("9e4e")
                    ),
                )
        elif header == self.accepts and opcode == 0xBF:
            value = int.from_bytes(frame[6:8], "little")
            self.target = 1000 - value if self.inverted else value
        elif header == self.accepts and opcode == 0x5F:
            self.target = self.raw


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(exchange, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(exchange, "REPLY_WAIT_SECONDS", 0)
    monkeypatch.setattr(exchange, "POLL_WAIT_SECONDS", 0)
    monkeypatch.setattr(exchange, "MIN_OBSERVE_SECONDS", 0)
    monkeypatch.setattr(exchange, "MOVE_TIMEOUT_SECONDS", 2)


async def _run(hass, motor, call):
    async def _connect(*args, **kwargs):
        return motor

    with (
        patch.object(exchange.bluetooth, "async_ble_device_from_address", return_value=object()),
        patch.object(exchange, "establish_connection", side_effect=_connect),
    ):
        return await call(hass)


async def test_goto_reaches_target(hass):
    motor = Motor()
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 90))

    assert result["outcome"] == "done"
    assert result["position_before"] == 100.0
    assert result["position_after"] == 90.0
    assert result["guard_tripped"] is False
    assert build_goto(HEADER_58, 90) == bytes.fromhex("ff58ea41bf038403")
    assert motor.written.count(build_goto(HEADER_58, 90)) == 1
    assert build_stop(HEADER_58) not in motor.written
    assert not motor.is_connected


async def test_goto_with_ignored_header_does_not_move(hass):
    motor = Motor(accepts=HEADER_78)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 90))

    assert result["outcome"] == "done"
    assert result["position_before"] == 100.0
    assert result["position_after"] == 100.0
    assert motor.raw == 1000


async def test_guard_stops_a_move_in_the_wrong_range(hass):
    # Asked for 90, but this motor heads for 10: it must be stopped on the way.
    motor = Motor(inverted=True)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 90))

    assert result["outcome"] == "guard_stop"
    assert result["guard_tripped"] is True
    assert build_stop(HEADER_58) in motor.written
    assert motor.target == motor.raw
    assert motor.raw > 700  # stopped shortly after leaving the 90..100 span
    assert result["position_after"] == motor.raw / 10


async def test_no_move_without_position_feedback(hass):
    motor = Motor(silent=True)
    result = await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 90))

    assert result["outcome"] == "no_status"
    assert motor.written == [build_read_status(HEADER_58)]


async def test_stop_sends_stop_and_reads_position(hass):
    motor = Motor()
    result = await _run(hass, motor, lambda h: exchange.async_stop(h, ADDRESS, "78"))

    assert result["outcome"] == "done"
    assert result["position_after"] == 100.0
    assert motor.written == [
        build_read_status(HEADER_78),
        build_stop(HEADER_78),
        build_read_status(HEADER_78),
    ]


async def test_only_documented_frames_are_written(hass):
    motor = Motor(inverted=True)
    await _run(hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 90))
    allowed_opcodes = {0xD1, 0xBF, 0x5F}
    assert {frame[4] for frame in motor.written} <= allowed_opcodes
    assert all(frame[:4] == HEADER_58 for frame in motor.written)


async def test_device_not_seen(hass):
    with patch.object(exchange.bluetooth, "async_ble_device_from_address", return_value=None):
        result = await exchange.async_goto(hass, ADDRESS, "58", 90)
    assert result["outcome"] == "device_not_found"


def test_goto_rejects_out_of_range():
    with pytest.raises(ValueError):
        build_goto(HEADER_58, 101)


async def test_stop_request_during_move_stops_the_motor(hass):
    import asyncio

    event = asyncio.Event()
    # Ask for the stop once the third status read has been answered.
    motor = Motor(on_read=lambda count: event.set() if count == 3 else None)
    result = await _run(
        hass, motor, lambda h: exchange.async_goto(h, ADDRESS, "58", 50, event)
    )

    assert result["outcome"] == "stopped"
    assert result["stopped_by_request"] is True
    assert result["guard_tripped"] is False
    assert motor.written.count(build_stop(HEADER_58)) == 1
    assert motor.target == motor.raw
    assert 50 < result["position_after"] < 100
    assert result["position_after"] == motor.raw / 10
    names = [step["step"] for step in result["steps"]]
    assert "stop_requested" in names
    assert all("at" in step for step in result["steps"])
