"""Fernet-based encryption for user API keys."""

import logging
import os
import warnings
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_KEY_FILE = Path(__file__).parent / "data" / ".fernet_key"


class KeyVault:
    """Encrypt/decrypt API keys using Fernet symmetric encryption.

    Master key resolution order:
    1. FERNET_KEY environment variable
    2. data/.fernet_key file (auto-generated on first run)
    """

    def __init__(self):
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        """Load master encryption key, or generate one on first run."""
        # Priority 1: Environment variable
        env_key = os.environ.get("FERNET_KEY")
        if env_key:
            return env_key.encode()

        # Priority 2: Key file
        if _KEY_FILE.exists():
            return _KEY_FILE.read_bytes().strip()

        # Auto-generate on first run
        key = Fernet.generate_key()
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_bytes(key)
        _KEY_FILE.chmod(0o600)  # Owner-only read/write

        warnings.warn(
            f"\n"
            f"  ========================================================\n"
            f"  FERNET ENCRYPTION KEY auto-generated at:\n"
            f"    {_KEY_FILE}\n"
            f"\n"
            f"  BACK THIS UP! If lost, all stored API keys become\n"
            f"  unrecoverable. Set FERNET_KEY env var in production.\n"
            f"  ========================================================\n",
            stacklevel=2,
        )
        return key

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string. Returns plaintext."""
        return self._fernet.decrypt(ciphertext.encode()).decode()


# Module-level singleton (created on import)
vault = KeyVault()
