"""Probe behaviour against a scripted motor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.roller22g import probe
from custom_components.roller22g.const import (
    COMMAND_UUID,
    RESPONSE_UUID,
    SERVICE_UUID,
)

ADDRESS = "EB:C0:E9:11:3D:A8"

LOGIN = bytes.fromhex("ff03030303787878787878")
STATUS_58 = bytes.fromhex("ff58ea41d10301")
STATUS_78 = bytes.fromhex("ff78ea41d10301")
ALLOWED = {LOGIN, STATUS_58, STATUS_78}


class FakeClient:
    """Stands in for a connected BleakClient.

    replies maps (frame, logged_in) to the notification the motor would send.
    drop_on is a set of frames that make the motor drop the link.
    """

    def __init__(self, replies, drop_on=(), has_service=True):
        self.replies = replies
        self.drop_on = set(drop_on)
        self.is_connected = True
        self.logged_in = False
        self.written = []
        self._notify = None
        chars = [
            SimpleNamespace(uuid=COMMAND_UUID, properties=["write"]),
            SimpleNamespace(uuid=RESPONSE_UUID, properties=["notify"]),
        ]
        service = SimpleNamespace(uuid=SERVICE_UUID, characteristics=chars)
        self.services = SimpleNamespace(
            get_service=lambda uuid: service if has_service else None
        )

    async def start_notify(self, uuid, cb):
        assert uuid == RESPONSE_UUID
        self._notify = cb

    async def stop_notify(self, uuid):
        self._notify = None

    async def write_gatt_char(self, uuid, data, response=False):
        assert uuid == COMMAND_UUID
        assert response is True
        frame = bytes(data)
        self.written.append(frame)
        if frame in self.drop_on:
            self.is_connected = False
            return
        reply = self.replies.get((frame, self.logged_in))
        if frame == LOGIN and reply and reply[6] > 0:
            self.logged_in = True
        if reply is not None and self._notify is not None:
            self._notify(None, bytearray(reply))

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(probe, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(probe, "STEP_WAIT_SECONDS", 0)


async def _run(hass, clients, device=object()):
    queue = list(clients)

    async def _connect(*args, **kwargs):
        return queue.pop(0)

    with (
        patch.object(probe.bluetooth, "async_ble_device_from_address", return_value=device),
        patch.object(probe.bluetooth, "async_last_service_info", return_value=None),
        patch.object(probe, "establish_connection", side_effect=_connect),
    ):
        return await probe.async_run_probe(hass, ADDRESS)


async def test_login_then_status_with_header_58(hass):
    client = FakeClient(
        {
            (LOGIN, False): bytes.fromhex("ff010203d40001"),
            (STATUS_58, True): bytes.fromhex("ff010203d10232f401"),
        }
    )
    result = await _run(hass, [client])

    assert result["verdict"] == "match_header_58"
    assert result["connections"] == 1
    assert result["service_found"] is True
    assert [s["step"] for s in result["steps"]] == [name for name, _ in probe.STEPS]
    after = next(s for s in result["steps"] if s["step"] == "status_58_after_login")
    assert after["rx"][0]["parsed"]["position_percent"] == 50
    assert after["rx"][0]["parsed"]["position_raw"] == 500
    before = next(s for s in result["steps"] if s["step"] == "status_58_before_login")
    assert before["rx"] == []
    assert not client.is_connected


async def test_status_without_login_with_header_78(hass):
    client = FakeClient(
        {
            (STATUS_78, False): bytes.fromhex("ff010203d20246bc02"),
            (STATUS_78, True): bytes.fromhex("ff010203d20246bc02"),
        }
    )
    result = await _run(hass, [client])
    assert result["verdict"] == "match_header_78"


async def test_login_accepted_but_no_status(hass):
    client = FakeClient({(LOGIN, False): bytes.fromhex("ff010203d40001")})
    result = await _run(hass, [client])
    assert result["verdict"] == "login_only"


async def test_silent_motor(hass):
    result = await _run(hass, [FakeClient({})])
    assert result["verdict"] == "no_response"


async def test_login_rejected(hass):
    client = FakeClient({(LOGIN, False): bytes.fromhex("ff010203d40000")})
    result = await _run(hass, [client])
    assert result["verdict"] == "no_response"
    login = next(s for s in result["steps"] if s["step"] == "login")
    assert login["rx"][0]["parsed"]["success"] is False


async def test_only_read_only_frames_are_ever_written(hass):
    client = FakeClient(
        {
            (LOGIN, False): bytes.fromhex("ff010203d40001"),
            (STATUS_58, True): bytes.fromhex("ff010203d10232f401"),
        }
    )
    await _run(hass, [client])
    assert client.written
    assert set(client.written) <= ALLOWED


async def test_resumes_from_login_when_link_drops_before_login(hass):
    first = FakeClient({}, drop_on={STATUS_58})
    second = FakeClient(
        {
            (LOGIN, False): bytes.fromhex("ff010203d40001"),
            (STATUS_58, True): bytes.fromhex("ff010203d10232f401"),
        }
    )
    result = await _run(hass, [first, second])

    assert result["connections"] == 2
    assert result["verdict"] == "match_header_58"
    assert first.written == [STATUS_58]
    assert second.written == [LOGIN, STATUS_58, STATUS_78]
    assert set(first.written + second.written) <= ALLOWED


async def test_device_not_seen(hass):
    result = await _run(hass, [], device=None)
    assert result["verdict"] == "device_not_found"
    assert result["steps"] == []


async def test_connect_failure(hass):
    from bleak.exc import BleakError

    with (
        patch.object(probe.bluetooth, "async_ble_device_from_address", return_value=object()),
        patch.object(probe.bluetooth, "async_last_service_info", return_value=None),
        patch.object(probe, "establish_connection", side_effect=BleakError("nope")),
    ):
        result = await probe.async_run_probe(hass, ADDRESS)
    assert result["verdict"] == "connect_failed"
    assert "nope" in result["error"]


async def test_service_missing(hass):
    client = FakeClient({}, has_service=False)
    result = await _run(hass, [client])
    assert result["service_found"] is False
    assert client.written == []
    assert result["verdict"] == "no_response"
