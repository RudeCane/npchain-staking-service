"""Tests for the auth module: replay protection + sig verify stubs."""

import time

import pytest

from src.staking.auth import (
    SignatureError,
    _canonical_message,
    _check_replay,
    _seen_nonces,
    _verify_dilithium_stub,
)


@pytest.fixture(autouse=True)
def clear_nonce_cache():
    _seen_nonces.clear()
    yield
    _seen_nonces.clear()


def test_canonical_message_format() -> None:
    msg = _canonical_message("UNSTAKE", "NPCabc", 1000, "nonce-1", 1714512000)
    assert msg == b"UNSTAKE|NPCabc|1000|nonce-1|1714512000"


def test_replay_check_accepts_fresh_nonce() -> None:
    now = int(time.time())
    _check_replay("fresh-nonce", now)
    # Should not raise
    assert "fresh-nonce" in _seen_nonces


def test_replay_check_rejects_reused_nonce() -> None:
    now = int(time.time())
    _check_replay("nonce-x", now)
    with pytest.raises(SignatureError, match="nonce_already_used"):
        _check_replay("nonce-x", now)


def test_replay_check_rejects_old_timestamp() -> None:
    old = int(time.time()) - 1000  # way outside the 300s window
    with pytest.raises(SignatureError, match="timestamp_outside_window"):
        _check_replay("any-nonce", old)


def test_replay_check_rejects_future_timestamp() -> None:
    future = int(time.time()) + 1000
    with pytest.raises(SignatureError, match="timestamp_outside_window"):
        _check_replay("future-nonce", future)


def test_replay_check_within_window() -> None:
    """Within +/- 300s, nonces should be accepted."""
    now = int(time.time())
    _check_replay("near-past", now - 100)
    _check_replay("near-future", now + 100)


def test_dilithium_stub_accepts_valid_hex() -> None:
    pubkey = "abcd1234" * 100  # 800-char hex
    sig = "deadbeef" * 50
    assert _verify_dilithium_stub(pubkey, b"some-message", sig) is True


def test_dilithium_stub_rejects_empty_signature() -> None:
    assert _verify_dilithium_stub("abcd" * 100, b"msg", "") is False


def test_dilithium_stub_rejects_short_signature() -> None:
    assert _verify_dilithium_stub("abcd" * 100, b"msg", "short") is False


def test_dilithium_stub_rejects_non_hex_signature() -> None:
    assert _verify_dilithium_stub("abcd" * 100, b"msg", "not_hex_!!!!") is False


def test_dilithium_stub_rejects_non_hex_pubkey() -> None:
    assert _verify_dilithium_stub("not_hex_pubkey", b"msg", "abcd" * 50) is False
