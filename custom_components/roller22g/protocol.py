"""Wire format used by the read-only probe.

Only frames that do not move the motor and do not change stored settings are
built here: login and read status. The layout follows the protocol notes of
viggfred/blissble (MIT) and mistermej/Bliss-Smartblinds (MIT), which document
motors that expose the same GATT characteristics under other names. Whether a
ROLLER22G motor uses the same constants is what the probe is there to find out.

    command  : <4-byte header> <opcode> <sub> <payload...>
    login    : FF 03 03 03 03 <password, at least 6 bytes, zero padded>
    response : FF 01 02 03 <opcode> <payload...>
"""

from __future__ import annotations

from typing import Any

# The two command headers documented for this motor family.
HEADER_58 = bytes.fromhex("ff58ea41")
HEADER_78 = bytes.fromhex("ff78ea41")

LOGIN_PREFIX = bytes.fromhex("ff03030303")
DEFAULT_PASSWORD = "xxxxxx"

RESPONSE_HEADER = bytes.fromhex("ff010203")

OP_READ_STATUS = 0xD1
OP_STATUS_PUSH = 0xD2
OP_PASSWORD_SET = 0xD3
OP_LOGIN_RESULT = 0xD4

_BATTERY = {0x00: "normal", 0x08: "low", 0x10: "none"}


def build_login(password: str = DEFAULT_PASSWORD) -> bytes:
    """Return the login frame for the given password."""
    raw = password.encode("ascii")
    if len(raw) < 6:
        raw = raw.ljust(6, b"\x00")
    return LOGIN_PREFIX + raw


def build_read_status(header: bytes) -> bytes:
    """Return the frame that asks the motor for a status report."""
    if len(header) != 4:
        raise ValueError("header must be 4 bytes")
    return header + bytes((OP_READ_STATUS, 0x03, 0x01))


def parse_response(frame: bytes) -> dict[str, Any]:
    """Decode a notification from the response characteristic.

    Unknown or malformed frames are reported as such rather than raised, so
    the probe can record whatever the motor sends.
    """
    data = bytes(frame)
    if len(data) < 5 or not data.startswith(RESPONSE_HEADER):
        return {"kind": "unrecognized"}

    payload = data[4:]
    opcode = payload[0]
    parsed: dict[str, Any] = {"opcode": f"{opcode:02x}"}

    if opcode in (OP_READ_STATUS, OP_STATUS_PUSH):
        if len(payload) < 3:
            return {**parsed, "kind": "status_truncated"}
        flags = payload[1]
        parsed.update(
            kind="status",
            flags=f"{flags:02x}",
            position_percent=payload[2],
            direction_reversed=bool(flags & 0x01),
            limits_set=bool(flags & 0x02),
            remote_linked=bool(flags & 0x04),
            battery=_BATTERY.get(flags & 0x18, "unknown"),
        )
        if len(payload) >= 5:
            parsed["position_raw"] = payload[3] | (payload[4] << 8)
        return parsed

    if opcode == OP_LOGIN_RESULT:
        if len(payload) < 3:
            return {**parsed, "kind": "login_result_truncated"}
        return {**parsed, "kind": "login_result", "success": payload[2] > 0}

    return {**parsed, "kind": "other"}
