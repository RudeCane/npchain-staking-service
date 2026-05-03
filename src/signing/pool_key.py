"""Pool wallet key loader.

The pool wallet's encrypted keyfile lives at:
    /home/npchainorg/npchain-staking/poolwallet.key.enc
Format: openssl enc -aes-256-cbc -salt -pbkdf2  (5984 bytes plaintext = 1952 PK + 4032 SK)

Passphrase delivery: NPCHAIN_POOL_PASSPHRASE environment variable.
In production, this is set via systemd EnvironmentFile=/etc/npchain-staking/secrets.env
(root:root, mode 0600). The service auto-restarts on crash without manual intervention.

On load(), the module:
  1. Reads NPCHAIN_POOL_PASSPHRASE (raises if unset)
  2. Decrypts poolwallet.key.enc
  3. Splits into PK + SK
  4. Derives address via libnpc_dilithium3
  5. Asserts derived address == settings.pool_address  (refuses to start otherwise)
  6. Caches the loaded key

All subsequent calls to get() return the cached PoolKey. The service holds the
decrypted SK in memory for its lifetime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from src.config import settings
from src.signing import dilithium

log = structlog.get_logger(__name__)

KEYFILE_PATH = Path("/home/npchainorg/npchain-staking/poolwallet.key.enc")
PASSPHRASE_ENV = "NPCHAIN_POOL_PASSPHRASE"


class PoolKeyError(RuntimeError):
    """Raised when the pool key cannot be loaded or fails validation."""


@dataclass(frozen=True)
class PoolKey:
    """Immutable pool wallet key material.

    SK is held in memory for the service lifetime. Don't log it. Don't
    serialize it. Don't pass the whole struct over IPC.
    """
    address: str
    public_key: bytes  # 1952 bytes
    secret_key: bytes  # 4032 bytes

    def sign(self, msg: bytes) -> bytes:
        """Sign a message with the pool's secret key. Returns 3309-byte sig."""
        return dilithium.sign(msg, self.public_key, self.secret_key)


def _decrypt_openssl_cbc_pbkdf2(path: Path, passphrase: str) -> bytes:
    """Decrypt OpenSSL Salted__ + AES-256-CBC + PBKDF2 file.

    Matches: openssl enc -aes-256-cbc -salt -pbkdf2 -in <plain> -out <enc>
    OpenSSL defaults: SHA-256, 10000 iterations, derive 48 bytes (32 key + 16 IV).
    """
    with path.open("rb") as f:
        data = f.read()
    if data[:8] != b"Salted__":
        raise PoolKeyError(f"{path} is not an OpenSSL Salted__ file")
    salt = data[8:16]
    ct = data[16:]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=48,
        salt=salt,
        iterations=10000,
    )
    dk = kdf.derive(passphrase.encode())
    key, iv = dk[:32], dk[32:48]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = cipher.update(ct) + cipher.finalize()
    unpad = PKCS7(128).unpadder()
    return unpad.update(padded) + unpad.finalize()


_CACHED: PoolKey | None = None


def load() -> PoolKey:
    """Load and validate the pool key. Cached after first call.

    Raises PoolKeyError on any failure (passphrase missing, decryption fails,
    keyfile size wrong, derived address doesn't match settings).
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    passphrase = os.environ.get(PASSPHRASE_ENV)
    if not passphrase:
        raise PoolKeyError(
            f"{PASSPHRASE_ENV} environment variable is unset. "
            "Set it via the systemd EnvironmentFile or the shell before service start."
        )

    if not KEYFILE_PATH.exists():
        raise PoolKeyError(f"keyfile not found: {KEYFILE_PATH}")

    try:
        kb = _decrypt_openssl_cbc_pbkdf2(KEYFILE_PATH, passphrase)
    except Exception as e:
        raise PoolKeyError(f"decryption failed: {e}") from e

    expected_size = dilithium.PK_SIZE + dilithium.SK_SIZE
    if len(kb) != expected_size:
        raise PoolKeyError(
            f"unexpected plaintext size {len(kb)} (expected {expected_size}). "
            "Was the keyfile written with vendor format (npchain-staking/native/pool_keygen)?"
        )

    pk = kb[:dilithium.PK_SIZE]
    sk = kb[dilithium.PK_SIZE:]

    derived = dilithium.derive_address(pk)
    if derived != settings.pool_address:
        raise PoolKeyError(
            f"derived address {derived} does not match settings.pool_address {settings.pool_address}. "
            "The keyfile is for a different wallet, or settings.pool_address is wrong."
        )

    log.info(
        "pool_key_loaded",
        address=derived,
        keyfile=str(KEYFILE_PATH),
        pk_size=len(pk),
        sk_size=len(sk),
    )

    _CACHED = PoolKey(address=derived, public_key=pk, secret_key=sk)
    return _CACHED


def reset_for_tests() -> None:
    """ONLY for use in test code — drops the cached key so tests can re-load."""
    global _CACHED
    _CACHED = None
