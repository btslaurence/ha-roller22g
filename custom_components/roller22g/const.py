"""Constants for the ROLLER22G integration."""

from __future__ import annotations

DOMAIN = "roller22g"

CONF_ADDRESS = "address"

LOCAL_NAME = "ROLLER22G"

# GATT layout observed on a ROLLER22G motor (nRF52, Nordic Secure DFU present).
SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1910"
COMMAND_UUID = "00010405-0405-0607-0809-0a0b0c0d1910"
RESPONSE_UUID = "00010304-0405-0607-0809-0a0b0c0d1910"

# Verdicts reported by the read-only probe.
VERDICT_NOT_RUN = "not_run"
VERDICT_RUNNING = "running"
VERDICT_MATCH_58 = "match_header_58"
VERDICT_MATCH_78 = "match_header_78"
VERDICT_LOGIN_ONLY = "login_only"
VERDICT_NO_RESPONSE = "no_response"
VERDICT_NOT_FOUND = "device_not_found"
VERDICT_CONNECT_FAILED = "connect_failed"
VERDICT_ERROR = "error"

VERDICTS = [
    VERDICT_NOT_RUN,
    VERDICT_RUNNING,
    VERDICT_MATCH_58,
    VERDICT_MATCH_78,
    VERDICT_LOGIN_ONLY,
    VERDICT_NO_RESPONSE,
    VERDICT_NOT_FOUND,
    VERDICT_CONNECT_FAILED,
    VERDICT_ERROR,
]

# Command headers that can be selected for motion frames.
HEADERS = {"58": bytes.fromhex("ff58ea41"), "78": bytes.fromhex("ff78ea41")}
DEFAULT_HEADER = "58"

# Outcomes reported for a stop or go-to-position exchange.
OUTCOME_NOT_RUN = "not_run"
OUTCOME_RUNNING = "running"
OUTCOME_DONE = "done"
OUTCOME_STOPPED = "stopped"
OUTCOME_NO_STATUS = "no_status"
OUTCOME_GUARD_STOP = "guard_stop"

OUTCOMES = [
    OUTCOME_NOT_RUN,
    OUTCOME_RUNNING,
    OUTCOME_DONE,
    OUTCOME_STOPPED,
    OUTCOME_NO_STATUS,
    OUTCOME_GUARD_STOP,
    VERDICT_NOT_FOUND,
    VERDICT_CONNECT_FAILED,
    VERDICT_ERROR,
]
