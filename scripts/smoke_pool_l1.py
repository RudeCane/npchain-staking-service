#!/usr/bin/env python3
"""Smoke test: pool wallet signs a real TRANSFER, L1 verifies and accepts.

Decrypts poolwallet.key.enc, derives address, builds canonical TRANSFER tx,
signs with vendor Dilithium3, POSTs to L1 /api/v1/tx/submit.

On success: prints L1 acceptance.
On failure: prints L1 rejection reason — tells us exactly what's wrong.
"""
from __future__ import annotations
import ctypes
import getpass
import hashlib
import os
import struct
import sys
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

BASE = "/home/npchainorg/npchain-staking"
LIB = f"{BASE}/native/libnpc_dilithium3.so"
KEYFILE = f"{BASE}/poolwallet.key.enc"
POOL_ADDR = "NPC1006639349b6aceb65fc78be92a404e41edb7019"
L1_RPC = "http://34.75.143.141:18333"

PK = 1952
SK = 4032
SIG = 3309

# --- Load .so -----------------------------------------------------------
lib = ctypes.CDLL(LIB)

lib.npc_dil3_derive_address.argtypes = [
    ctypes.c_char_p, ctypes.c_size_t,
    ctypes.c_char_p, ctypes.c_size_t,
]
lib.npc_dil3_derive_address.restype = ctypes.c_int

lib.npc_dil3_sign.argtypes = [
    ctypes.c_char_p, ctypes.c_size_t,  # msg
    ctypes.c_char_p, ctypes.c_size_t,  # pk
    ctypes.c_char_p, ctypes.c_size_t,  # sk
    ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t),  # out_sig
]
lib.npc_dil3_sign.restype = ctypes.c_int

def derive_address(pk: bytes) -> str:
    buf = ctypes.create_string_buffer(48)
    if lib.npc_dil3_derive_address(pk, len(pk), buf, 48) != 1:
        raise RuntimeError("derive_address failed")
    return buf.value.decode("ascii")

def sign(msg: bytes, pk: bytes, sk: bytes) -> bytes:
    out = ctypes.create_string_buffer(SIG)
    outlen = ctypes.c_size_t(SIG)
    if lib.npc_dil3_sign(msg, len(msg), pk, len(pk), sk, len(sk),
                         out, ctypes.byref(outlen)) != 1:
        raise RuntimeError("sign failed")
    return bytes(out.raw[:outlen.value])

# --- OpenSSL AES-256-CBC + PBKDF2 (matches `openssl enc -aes-256-cbc -salt -pbkdf2`) ---
def decrypt_keyfile(path: str, passphrase: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"Salted__":
        raise ValueError("not an OpenSSL Salted__ file")
    salt = data[8:16]
    ct = data[16:]
    # Default OpenSSL: SHA-256, 10000 iterations, derive 48 bytes (32 key + 16 iv)
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
    unp = PKCS7(128).unpadder()
    return unp.update(padded) + unp.finalize()

# --- Canonical tx serialization (matches Transaction::serialize in testnet_node.cpp) ---
def serialize_tx(tx: dict) -> bytes:
    out = bytearray()
    out.append(tx["type"])
    out += struct.pack("<Q", tx["timestamp"])
    out += struct.pack("<Q", tx["nonce"])
    from_b = tx["from"].encode("ascii")
    out += struct.pack("<Q", len(from_b)) + from_b
    to_b = tx["to"].encode("ascii")
    out += struct.pack("<Q", len(to_b)) + to_b
    out += struct.pack("<Q", tx["amount"])
    out += struct.pack("<Q", tx["fee"])
    memo_b = tx["memo"].encode("utf-8")
    out += struct.pack("<Q", len(memo_b)) + memo_b
    data_b = tx["data"].encode("utf-8")
    out += struct.pack("<Q", len(data_b)) + data_b
    return bytes(out)

def main() -> int:
    passphrase = os.environ.get("NPCHAIN_POOL_PASSPHRASE")
    if not passphrase:
        passphrase = getpass.getpass("Pool passphrase: ")

    print("[1/6] decrypt keyfile...")
    kb = decrypt_keyfile(KEYFILE, passphrase)
    if len(kb) != PK + SK:
        print(f"  ERROR: keyfile size {len(kb)}, expected {PK + SK}")
        return 1
    pk = kb[:PK]
    sk = kb[PK:]
    print(f"  pk={len(pk)} sk={len(sk)}")

    print("[2/6] derive address...")
    addr = derive_address(pk)
    print(f"  derived = {addr}")
    print(f"  expected = {POOL_ADDR}")
    if addr != POOL_ADDR:
        print("  ERROR: address mismatch")
        return 1
    print("  MATCH")

    print("[3/6] build canonical TRANSFER...")
    tx = {
        "type": 0,                # TRANSFER
        "timestamp": int(time.time()),
        "nonce": 1,                # pool first tx
        "from": POOL_ADDR,
        "to": POOL_ADDR,           # self-send
        "amount": 100000000,  # 1 NPC
        "fee": 100_000_000,        # 1 NPC, generous
        "memo": "smoke-test",
        "data": "",
    }
    canon = serialize_tx(tx)
    print(f"  canonical_len = {len(canon)}")

    print("[4/6] tx_hash = sha3_256(canonical)...")
    tx_hash = hashlib.sha3_256(canon).digest()
    print(f"  tx_hash = {tx_hash.hex()}")

    print("[5/6] sign tx_hash with pool SK...")
    sig = sign(tx_hash, pk, sk)
    print(f"  sig_len = {len(sig)}")

    print("[6/6] POST /api/v1/tx/submit ...")
    payload = {
        "type": "transfer",
        "from": tx["from"],
        "to": tx["to"],
        "amount": str(tx["amount"]),
        "fee": str(tx["fee"]),
        "nonce": str(tx["nonce"]),
        "timestamp": str(tx["timestamp"]),
        "memo": tx["memo"],
        "data": tx["data"],
        "dilithium_sig": sig.hex(),
        "sender_pubkey": pk.hex(),
    }
    r = httpx.post(f"{L1_RPC}/api/v1/tx/submit", json=payload, timeout=30.0)
    print(f"  HTTP {r.status_code}")
    print(f"  body: {r.text}")
    return 0 if r.is_success and "error" not in r.text.lower() else 1

if __name__ == "__main__":
    sys.exit(main())