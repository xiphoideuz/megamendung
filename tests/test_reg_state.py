"""Offline tests for the MEGA registration state (de)serialization format."""

from __future__ import annotations

import pytest

from megamendung import mega_api


def test_reg_state_roundtrip():
    state = mega_api.RegState(
        user_handle="AbCdEf012345",
        password_key=b"\x01" * 16,
        challenge=b"\x02" * 16,
        email="me+mega1@gmail.com",
    )
    serialized = state.serialize()
    assert serialized.count(":") == 2
    pk, ch, handle = serialized.split(":")
    assert len(pk) == 24 and len(ch) == 24
    assert handle == "AbCdEf012345"
    restored = mega_api.RegState.deserialize(serialized, "me+mega1@gmail.com")
    assert restored.user_handle == state.user_handle
    assert restored.password_key == state.password_key
    assert restored.challenge == state.challenge
    assert restored.email == "me+mega1@gmail.com"


def test_reg_state_rejects_garbage():
    with pytest.raises(mega_api.MegaApiError):
        mega_api.RegState.deserialize("not-a-state", "a@b.com")


def test_reg_state_rejects_wrong_key_length():
    with pytest.raises(mega_api.MegaApiError):
        mega_api.RegState.deserialize(
            "QUJDREVGR0hJSktMTU5PUFGjZQ==:YWJjZGVmZ2hpamtsbW5v=",
            "a@b.com",
        )