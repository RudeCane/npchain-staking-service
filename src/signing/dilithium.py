"""Python ctypes wrapper around libnpc_dilithium3.so.

All Dilithium3 operations route through the vendor pq-crystals reference
impl (FIPS 204 ML-DSA-65), identical to what the L1 verifier accepts and
what the wallet's @noble/post-quantum produces.

Single shared library handle, loaded once at module import time. Functions
are pure: they accept bytes, return bytes, and never touch global state.

Sizes (FIPS 204 ML-DSA-65):
  PK:  1952 bytes
  SK:  4032 bytes
  SIG: 3309 bytes
  Address: "NPC" + hex(sha3_256(pk)[0..20]) = 43 chars

Signing context is always empty (matches L1 verifier and wallet path).
"""
from __future__ import annotations

import ctypes
from pathlib import Path

PK_SIZE = 1952
SK_SIZE = 4032
SIG_SIZE = 3309
ADDR_LEN = 43  # "NPC" + 40 hex

_LIB_PATH = Path(__file__).resolve().parent.parent.parent / "native" / "libnpc_dilithium3.so"


class DilithiumError(RuntimeError):
    """Raised on any failure inside the .so."""


def _load_lib() -> ctypes.CDLL:
    if not _LIB_PATH.exists():
        raise DilithiumError(
            f"shared library not found at {_LIB_PATH}. "
            "Build it with: cd native && make"
        )
    lib = ctypes.CDLL(str(_LIB_PATH))

    lib.npc_dil3_pk_size.restype = ctypes.c_size_t
    lib.npc_dil3_sk_size.restype = ctypes.c_size_t
    lib.npc_dil3_sig_size.restype = ctypes.c_size_t

    lib.npc_dil3_keygen.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
    ]
    lib.npc_dil3_keygen.restype = ctypes.c_int

    lib.npc_dil3_derive_address.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
    ]
    lib.npc_dil3_derive_address.restype = ctypes.c_int

    lib.npc_dil3_sign.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.npc_dil3_sign.restype = ctypes.c_int

    lib.npc_dil3_verify.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
    ]
    lib.npc_dil3_verify.restype = ctypes.c_int

    lib.npc_dil3_self_test.restype = ctypes.c_int

    # Sanity: confirm the .so reports the sizes we expect. Catches version drift.
    if lib.npc_dil3_pk_size() != PK_SIZE:
        raise DilithiumError(f"libnpc_dilithium3.so PK_SIZE mismatch: {lib.npc_dil3_pk_size()} vs {PK_SIZE}")
    if lib.npc_dil3_sk_size() != SK_SIZE:
        raise DilithiumError(f"libnpc_dilithium3.so SK_SIZE mismatch: {lib.npc_dil3_sk_size()} vs {SK_SIZE}")
    if lib.npc_dil3_sig_size() != SIG_SIZE:
        raise DilithiumError(f"libnpc_dilithium3.so SIG_SIZE mismatch: {lib.npc_dil3_sig_size()} vs {SIG_SIZE}")

    return lib


_lib = _load_lib()


def keygen() -> tuple[bytes, bytes]:
    """Generate a fresh keypair. Returns (pk, sk)."""
    pk_buf = ctypes.create_string_buffer(PK_SIZE)
    sk_buf = ctypes.create_string_buffer(SK_SIZE)
    if _lib.npc_dil3_keygen(pk_buf, PK_SIZE, sk_buf, SK_SIZE) != 1:
        raise DilithiumError("keygen failed")
    return bytes(pk_buf.raw[:PK_SIZE]), bytes(sk_buf.raw[:SK_SIZE])


def derive_address(pk: bytes) -> str:
    """Derive NPC address from public key: 'NPC' + hex(sha3_256(pk)[0..20])."""
    if len(pk) != PK_SIZE:
        raise DilithiumError(f"pk must be {PK_SIZE} bytes (got {len(pk)})")
    out = ctypes.create_string_buffer(48)
    if _lib.npc_dil3_derive_address(pk, PK_SIZE, out, 48) != 1:
        raise DilithiumError("derive_address failed")
    return out.value.decode("ascii")


def sign(msg: bytes, pk: bytes, sk: bytes) -> bytes:
    """Sign msg with sk. Returns 3309-byte FIPS 204 signature."""
    if len(pk) != PK_SIZE:
        raise DilithiumError(f"pk must be {PK_SIZE} bytes (got {len(pk)})")
    if len(sk) != SK_SIZE:
        raise DilithiumError(f"sk must be {SK_SIZE} bytes (got {len(sk)})")
    out = ctypes.create_string_buffer(SIG_SIZE)
    outlen = ctypes.c_size_t(SIG_SIZE)
    if _lib.npc_dil3_sign(msg, len(msg), pk, PK_SIZE, sk, SK_SIZE,
                          out, ctypes.byref(outlen)) != 1:
        raise DilithiumError("sign failed")
    return bytes(out.raw[:outlen.value])


def verify(msg: bytes, sig: bytes, pk: bytes) -> bool:
    """Verify a signature. Returns True if valid."""
    if len(pk) != PK_SIZE:
        return False
    return _lib.npc_dil3_verify(msg, len(msg), sig, len(sig), pk, PK_SIZE) == 1


def self_test() -> bool:
    """Round-trip test: keygen -> sign -> verify. Returns True on pass."""
    return _lib.npc_dil3_self_test() == 1
