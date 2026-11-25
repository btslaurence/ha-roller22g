"""Wire-format tests. Expected bytes are taken from the blissble protocol notes."""

import pytest

from custom_components.roller22g.protocol import (
    HEADER_58,
    HEADER_78,
    build_login,
    build_read_status,
    parse_response,
)


def test_login_frame_default_password():
    assert build_login() == bytes.fromhex("ff03030303787878787878")


def test_login_frame_pads_short_password():
    assert build_login("ab") == bytes.fromhex("ff03030303") + b"ab\x00\x00\x00\x00"


def test_read_status_frames():
    assert build_read_status(HEADER_58) == bytes.fromhex("ff58ea41d10301")
    assert build_read_status(HEADER_78) == bytes.fromhex("ff78ea41d10301")


def test_read_status_rejects_bad_header():
    with pytest.raises(ValueError):
        build_read_status(b"\xff")


def test_parse_status_push():
    # Documented example: opcode d2, flags 02, 70 %, raw 700.
    parsed = parse_response(bytes.fromhex("ff010203d20246bc02"))
    assert parsed["kind"] == "status"
    assert parsed["opcode"] == "d2"
    assert parsed["position_percent"] == 70
    assert parsed["position_raw"] == 700
    assert parsed["limits_set"] is True
    assert parsed["direction_reversed"] is False
    assert parsed["battery"] == "normal"
    assert "extra" not in parsed


def test_parse_status_keeps_trailing_bytes():
    # Frame captured from a ROLLER22G motor: two bytes after the raw position.
    parsed = parse_response(bytes.fromhex("ff010203d10264e8039e4e"))
    assert parsed["position_raw"] == 1000
    assert parsed["extra"] == "9e 4e"


def test_parse_status_battery_low():
    parsed = parse_response(bytes.fromhex("ff010203d10a00"))
    assert parsed["kind"] == "status"
    assert parsed["battery"] == "low"
    assert "position_raw" not in parsed


def test_parse_login_results():
    assert parse_response(bytes.fromhex("ff010203d40001"))["success"] is True
    assert parse_response(bytes.fromhex("ff010203d40000"))["success"] is False


def test_parse_unknown_and_malformed():
    assert parse_response(bytes.fromhex("ff010203de0000"))["kind"] == "other"
    assert parse_response(b"\x01\x02")["kind"] == "unrecognized"
    assert parse_response(bytes.fromhex("aa010203d4"))["kind"] == "unrecognized"
    assert parse_response(bytes.fromhex("ff010203d4"))["kind"] == "login_result_truncated"
