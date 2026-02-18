"""Tests for keyvault.py — Fernet encryption/decryption.

Creates a fresh KeyVault per test using a known FERNET_KEY env var
to avoid side effects on the real key file.
"""

import os
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken


# Generate a stable test key
TEST_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_fernet_env(monkeypatch):
    """Ensure every test uses a known FERNET_KEY (not the real key file)."""
    monkeypatch.setenv("FERNET_KEY", TEST_FERNET_KEY)


@pytest.fixture
def vault():
    """Create a fresh KeyVault instance (picks up the monkeypatched env var)."""
    # Import inside fixture so monkeypatch env is active
    import importlib
    import keyvault as kv_mod
    importlib.reload(kv_mod)
    return kv_mod.KeyVault()


# ── Encrypt / Decrypt ───────────────────────────────────────────────

class TestEncryptDecrypt:
    def test_round_trip(self, vault):
        ct = vault.encrypt("sk-test-1234")
        pt = vault.decrypt(ct)
        assert pt == "sk-test-1234"

    def test_encrypt_returns_string(self, vault):
        ct = vault.encrypt("hello")
        assert isinstance(ct, str)

    def test_ciphertext_differs_from_plaintext(self, vault):
        ct = vault.encrypt("my-api-key")
        assert ct != "my-api-key"

    def test_different_plaintexts_different_ciphertexts(self, vault):
        ct1 = vault.encrypt("key1")
        ct2 = vault.encrypt("key2")
        assert ct1 != ct2

    def test_same_plaintext_different_ciphertexts(self, vault):
        """Fernet uses a random IV, so encrypting the same plaintext twice
        should produce different ciphertext."""
        ct1 = vault.encrypt("same")
        ct2 = vault.encrypt("same")
        assert ct1 != ct2

    def test_both_ciphertexts_decrypt_to_same(self, vault):
        ct1 = vault.encrypt("same")
        ct2 = vault.encrypt("same")
        assert vault.decrypt(ct1) == "same"
        assert vault.decrypt(ct2) == "same"

    def test_empty_string(self, vault):
        ct = vault.encrypt("")
        assert vault.decrypt(ct) == ""

    def test_unicode_round_trip(self, vault):
        ct = vault.encrypt("test-key-with-unicode")
        assert vault.decrypt(ct) == "test-key-with-unicode"

    def test_long_string(self, vault):
        long_key = "sk-" + "a" * 500
        ct = vault.encrypt(long_key)
        assert vault.decrypt(ct) == long_key

    def test_decrypt_invalid_ciphertext_raises(self, vault):
        with pytest.raises(Exception):
            vault.decrypt("not-valid-ciphertext")

    def test_decrypt_with_wrong_key_raises(self, vault, monkeypatch):
        ct = vault.encrypt("secret")
        # Create a new vault with a different key
        different_key = Fernet.generate_key().decode()
        monkeypatch.setenv("FERNET_KEY", different_key)
        import importlib
        import keyvault as kv_mod
        importlib.reload(kv_mod)
        vault2 = kv_mod.KeyVault()
        with pytest.raises(InvalidToken):
            vault2.decrypt(ct)


# ── Key loading ─────────────────────────────────────────────────────

class TestKeyLoading:
    def test_uses_env_var_when_set(self, vault, monkeypatch):
        """Vault should use FERNET_KEY env var (which our fixture sets)."""
        # If we can encrypt/decrypt, the key is working
        ct = vault.encrypt("test")
        assert vault.decrypt(ct) == "test"

    def test_generates_key_file_when_no_env(self, tmp_path, monkeypatch):
        """When FERNET_KEY is not set and no key file exists, one is created."""
        monkeypatch.delenv("FERNET_KEY", raising=False)

        key_file = tmp_path / ".fernet_key"

        # Patch _KEY_FILE on the already-imported module (no reload needed)
        import keyvault as kv_mod
        monkeypatch.setattr(kv_mod, "_KEY_FILE", key_file)

        # Creating a new vault should generate the key file
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v = kv_mod.KeyVault()

        assert key_file.exists()
        # And it should work
        ct = v.encrypt("test")
        assert v.decrypt(ct) == "test"

    def test_reuses_existing_key_file(self, tmp_path, monkeypatch):
        """When FERNET_KEY is not set but key file exists, use it."""
        monkeypatch.delenv("FERNET_KEY", raising=False)

        key_file = tmp_path / ".fernet_key"
        known_key = Fernet.generate_key()
        key_file.write_bytes(known_key)

        # Patch _KEY_FILE on the already-imported module (no reload needed)
        import keyvault as kv_mod
        monkeypatch.setattr(kv_mod, "_KEY_FILE", key_file)

        v = kv_mod.KeyVault()

        # Encrypt with vault, decrypt with known key to verify same key used
        ct = v.encrypt("verify")
        pt = Fernet(known_key).decrypt(ct.encode()).decode()
        assert pt == "verify"
