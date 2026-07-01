#!/usr/bin/env python3
"""Development beacon server for the asymmetric entropy capsule artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import struct
import time

try:
    from nacl.signing import SigningKey
except Exception:  # pragma: no cover
    SigningKey = None

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except Exception:  # pragma: no cover
    Ed25519PrivateKey = None


HELLO_MAGIC = b"TEBH"
CAPSULE_MAGIC = b"TEBC"
VERSION = 1
PROFILE_DEV_ED25519_PUF_BEACON = 1
HELLO_LEN = 88
CAPSULE_SIGNED_LEN = 96
CAPSULE_LEN = 160
DEV_SEED = bytes.fromhex(
    "54454220646576207369676e696e672073656564203230323621212121212121"
)
DEV_PUBLIC = bytes.fromhex(
    "91b55729fd6d881df8dc43ee8603280b9546e875fb183e533d0fa5e9b5d3eb1d"
)


def sign_message(msg: bytes) -> bytes:
    if SigningKey is not None:
        signed = SigningKey(DEV_SEED).sign(msg)
        return bytes(signed.signature)

    if Ed25519PrivateKey is not None:
        return Ed25519PrivateKey.from_private_bytes(DEV_SEED).sign(msg)

    raise RuntimeError("install PyNaCl or cryptography for Ed25519 signing")


def parse_hello(data: bytes) -> dict[str, object]:
    if len(data) != HELLO_LEN:
        raise ValueError(f"bad hello length {len(data)}")
    magic, version, profile, flags, device_id, counter, uptime = struct.unpack(
        "!4sBBH Q I I", data[:24]
    )
    if magic != HELLO_MAGIC:
        raise ValueError("bad hello magic")
    if version != VERSION:
        raise ValueError(f"bad version {version}")
    if profile != PROFILE_DEV_ED25519_PUF_BEACON:
        raise ValueError(f"bad profile {profile}")
    return {
        "flags": flags,
        "device_id": device_id,
        "counter": counter,
        "uptime": uptime,
        "sram_commitment": data[24:56],
        "timing_commitment": data[56:88],
        "hello_hash": hashlib.sha256(data).digest(),
    }


def build_capsule(hello: dict[str, object], sequence: int) -> bytes:
    beacon = os.urandom(32)
    gateway_time_ms = int(time.time() * 1000)
    signed = struct.pack(
        "!4sBBH Q I Q I 32s 32s",
        CAPSULE_MAGIC,
        VERSION,
        PROFILE_DEV_ED25519_PUF_BEACON,
        CAPSULE_SIGNED_LEN,
        int(hello["device_id"]),
        int(hello["counter"]),
        gateway_time_ms,
        sequence,
        beacon,
        hello["hello_hash"],
    )
    sig = sign_message(signed)
    if len(signed) != CAPSULE_SIGNED_LEN or len(sig) != 64:
        raise AssertionError("capsule size bug")
    return signed + sig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6767)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))

    print(f"[TEB_SERVER] bind,{args.bind}:{args.port}", flush=True)
    print("[TEB_SERVER] profile,dev-ed25519-puf-beacon", flush=True)
    print(f"[TEB_SERVER] public_key,{DEV_PUBLIC.hex()}", flush=True)

    sequence = 0
    while True:
        data, peer = sock.recvfrom(2048)
        try:
            hello = parse_hello(data)
            sequence += 1
            capsule = build_capsule(hello, sequence)
            sock.sendto(capsule, peer)
            print(
                "[TEB_SERVER] served,"
                f"peer={peer[0]}:{peer[1]},"
                f"device=0x{int(hello['device_id']):016x},"
                f"counter={int(hello['counter'])},"
                f"seq={sequence}",
                flush=True,
            )
        except Exception as exc:
            print(f"[TEB_SERVER] reject,peer={peer},error={exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
