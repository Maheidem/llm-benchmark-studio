"""Tests for auth.py — pure utility functions.

Tests LoginRateLimiter, password hashing/verification, JWT token
creation/decode, and hash_token. Does NOT test FastAPI route handlers
(those require a running app with DB).
"""

import time
import pytest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from auth import (
    LoginRateLimiter,
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    JWT_SECRET,
    JWT_ALGORITHM,
)
from jose import jwt, JWTError


# ── LoginRateLimiter ────────────────────────────────────────────────

class TestLoginRateLimiter:
    def test_allows_first_attempt(self):
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        allowed, retry = limiter.check("10.0.0.1")
        assert allowed is True
        assert retry == 0

    def test_allows_up_to_max_attempts(self):
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        ip = "10.0.0.2"
        for _ in range(2):
            limiter.record_attempt(ip)
        allowed, _ = limiter.check(ip)
        assert allowed is True

    def test_blocks_after_max_attempts(self):
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        ip = "10.0.0.3"
        for _ in range(3):
            limiter.record_attempt(ip)
        allowed, retry = limiter.check(ip)
        assert allowed is False
        assert retry > 0

    def test_lockout_retry_after_value(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, lockout_seconds=300)
        ip = "10.0.0.4"
        for _ in range(2):
            limiter.record_attempt(ip)
        allowed, retry = limiter.check(ip)
        assert allowed is False
        assert retry <= 300
        assert retry > 0

    def test_lockout_persists_within_window(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, lockout_seconds=300)
        ip = "10.0.0.5"
        for _ in range(2):
            limiter.record_attempt(ip)
        limiter.check(ip)  # triggers lockout
        allowed, _ = limiter.check(ip)  # second check
        assert allowed is False

    def test_lockout_expires(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=10, lockout_seconds=1)
        ip = "10.0.0.6"
        for _ in range(2):
            limiter.record_attempt(ip)
        limiter.check(ip)  # triggers lockout

        # Manually expire the lockout AND clear old attempts
        limiter._lockouts[ip] = time.time() - 1
        limiter._attempts[ip] = []
        allowed, _ = limiter.check(ip)
        assert allowed is True

    def test_old_attempts_pruned(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=10, lockout_seconds=60)
        ip = "10.0.0.7"
        # Add old attempts
        limiter._attempts[ip] = [time.time() - 20, time.time() - 15]
        allowed, _ = limiter.check(ip)
        assert allowed is True
        # Old attempts should be pruned
        assert len(limiter._attempts[ip]) == 0

    def test_different_ips_independent(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, lockout_seconds=120)
        ip1 = "10.0.0.8"
        ip2 = "10.0.0.9"
        for _ in range(2):
            limiter.record_attempt(ip1)
        limiter.check(ip1)  # locks out ip1
        allowed, _ = limiter.check(ip2)
        assert allowed is True


# ── Password helpers ────────────────────────────────────────────────

class TestPasswordHelpers:
    def test_hash_password_returns_string(self):
        h = hash_password("test1234")
        assert isinstance(h, str)
        assert h.startswith("$2")  # bcrypt prefix

    def test_verify_password_correct(self):
        h = hash_password("mysecret")
        assert verify_password("mysecret", h) is True

    def test_verify_password_wrong(self):
        h = hash_password("mysecret")
        assert verify_password("wrong", h) is False

    def test_hash_password_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt

    def test_verify_both_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


# ── Token helpers ───────────────────────────────────────────────────

class TestTokenHelpers:
    def test_create_access_token_decodable(self):
        token = create_access_token("user123", "admin")
        payload = decode_token(token)
        assert payload["sub"] == "user123"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_create_refresh_token_decodable(self):
        token = create_refresh_token("user456")
        payload = decode_token(token)
        assert payload["sub"] == "user456"
        assert payload["type"] == "refresh"

    def test_access_token_has_expiry(self):
        token = create_access_token("u1", "user")
        payload = decode_token(token)
        assert "exp" in payload

    def test_refresh_token_has_expiry(self):
        token = create_refresh_token("u1")
        payload = decode_token(token)
        assert "exp" in payload

    def test_decode_expired_token_raises(self):
        # Create a token that's already expired
        expired_payload = {
            "sub": "u1",
            "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "type": "access",
        }
        token = jwt.encode(expired_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        with pytest.raises(Exception):
            decode_token(token)

    def test_decode_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("not.a.valid.jwt")

    def test_decode_wrong_secret_raises(self):
        payload = {"sub": "u1", "role": "user", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        token = jwt.encode(payload, "wrong-secret", algorithm=JWT_ALGORITHM)
        with pytest.raises(JWTError):
            decode_token(token)


# ── hash_token ──────────────────────────────────────────────────────

class TestHashToken:
    def test_returns_hex_string(self):
        h = hash_token("some-jwt-token")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest

    def test_deterministic(self):
        h1 = hash_token("abc")
        h2 = hash_token("abc")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        h1 = hash_token("token1")
        h2 = hash_token("token2")
        assert h1 != h2
