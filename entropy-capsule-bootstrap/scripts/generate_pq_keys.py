#!/usr/bin/env python3
"""Generate deterministic lab keys for the PQ entropy capsule benchmark."""

from __future__ import annotations

import ctypes
import hashlib
import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

MLKEM512_PUBLIC_KEY_SIZE = 800
MLKEM512_PRIVATE_KEY_SIZE = 1632
MLKEM512_CIPHERTEXT_SIZE = 768
MLKEM512_SHARED_SECRET_SIZE = 32
MLDSA44_PUBLIC_KEY_SIZE = 1312
MLDSA44_SECRET_KEY_SIZE = 2560
MLDSA44_SIG_SIZE = 2420


def u8_array(size: int):
    return (ctypes.c_uint8 * size)()


class DeterministicRng:
    def __init__(self, seed: bytes) -> None:
        digest = hashlib.sha512(seed).digest()
        self.state = int.from_bytes(digest[:8], "little")

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def fill(self, out, length: int) -> None:
        offset = 0
        while offset < length:
            block = self.next_u64().to_bytes(8, "little")
            for value in block:
                if offset >= length:
                    return
                out[offset] = value
                offset += 1


def format_array(name: str, data: bytes, size_macro: str) -> str:
    rows = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        rows.append("\t" + ", ".join(f"0x{byte:02x}" for byte in chunk))
    return (
        f"static const uint8_t {name}[{size_macro}] = {{\n"
        + ",\n".join(rows)
        + "\n};\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic lab keys for the PQ entropy capsule profile"
    )
    return parser.parse_args()


def main() -> int:
    parse_args()
    oqs = ctypes.CDLL("liboqs.so")

    oqs.OQS_KEM_ml_kem_512_keypair_derand.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
    ]
    oqs.OQS_KEM_ml_kem_512_encaps.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
    ]
    oqs.OQS_KEM_ml_kem_512_decaps.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
    ]
    oqs.OQS_SIG_ml_dsa_44_keypair.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
    ]
    oqs.OQS_SIG_ml_dsa_44_sign.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8),
    ]
    oqs.OQS_SIG_ml_dsa_44_verify.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8),
    ]

    rng = DeterministicRng(b"entropy capsule deterministic ML-DSA-44 lab key v1")
    callback_type = ctypes.CFUNCTYPE(
        None, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t
    )
    rng_callback = callback_type(rng.fill)
    oqs.OQS_randombytes_custom_algorithm(rng_callback)

    kem_seed = hashlib.sha512(
        b"entropy capsule deterministic ML-KEM-512 lab key v1"
    ).digest()
    kem_seed_buf = (ctypes.c_uint8 * len(kem_seed)).from_buffer_copy(kem_seed)
    kem_public = u8_array(MLKEM512_PUBLIC_KEY_SIZE)
    kem_private = u8_array(MLKEM512_PRIVATE_KEY_SIZE)
    if (
        oqs.OQS_KEM_ml_kem_512_keypair_derand(kem_public, kem_private, kem_seed_buf)
        != 0
    ):
        raise RuntimeError("ML-KEM-512 key generation failed")

    mldsa_public = u8_array(MLDSA44_PUBLIC_KEY_SIZE)
    mldsa_secret = u8_array(MLDSA44_SECRET_KEY_SIZE)
    if oqs.OQS_SIG_ml_dsa_44_keypair(mldsa_public, mldsa_secret) != 0:
        raise RuntimeError("ML-DSA-44 key generation failed")

    ct = u8_array(MLKEM512_CIPHERTEXT_SIZE)
    ss_a = u8_array(MLKEM512_SHARED_SECRET_SIZE)
    ss_b = u8_array(MLKEM512_SHARED_SECRET_SIZE)
    if oqs.OQS_KEM_ml_kem_512_encaps(ct, ss_a, kem_public) != 0:
        raise RuntimeError("ML-KEM-512 encapsulation self-test failed")
    if oqs.OQS_KEM_ml_kem_512_decaps(ss_b, ct, kem_private) != 0:
        raise RuntimeError("ML-KEM-512 decapsulation self-test failed")
    if bytes(ss_a) != bytes(ss_b):
        raise RuntimeError("ML-KEM-512 self-test shared-secret mismatch")

    message = b"PQ entropy capsule key self-test"
    message_buf = (ctypes.c_uint8 * len(message)).from_buffer_copy(message)
    sig = u8_array(MLDSA44_SIG_SIZE)
    sig_len = ctypes.c_size_t()
    if (
        oqs.OQS_SIG_ml_dsa_44_sign(
            sig, ctypes.byref(sig_len), message_buf, len(message), mldsa_secret
        )
        != 0
    ):
        raise RuntimeError("ML-DSA-44 signing self-test failed")
    if sig_len.value != MLDSA44_SIG_SIZE:
        raise RuntimeError(f"unexpected ML-DSA-44 signature size {sig_len.value}")
    if (
        oqs.OQS_SIG_ml_dsa_44_verify(
            message_buf, len(message), sig, sig_len.value, mldsa_public
        )
        != 0
    ):
        raise RuntimeError("ML-DSA-44 verification self-test failed")

    client_header = (
        "#ifndef TEB_PQ_CLIENT_KEYS_H\n"
        "#define TEB_PQ_CLIENT_KEYS_H\n\n"
        "#include <stdint.h>\n\n"
        '#include "teb_protocol.h"\n\n'
        "/* Deterministic lab-only keys for reproducible ESP32 benchmarks. */\n"
        + format_array(
            "teb_mlkem512_private_key",
            bytes(kem_private),
            "TEB_MLKEM512_PRIVATE_KEY_SIZE",
        )
        + "\n"
        + format_array(
            "teb_mldsa44_public_key",
            bytes(mldsa_public),
            "TEB_MLDSA44_PUBLIC_KEY_SIZE",
        )
        + "\n#endif /* TEB_PQ_CLIENT_KEYS_H */\n"
    )

    server_header = (
        "#ifndef TEB_PQ_SERVER_KEYS_H\n"
        "#define TEB_PQ_SERVER_KEYS_H\n\n"
        "#include <stdint.h>\n\n"
        "#define TEB_SERVER_MLKEM512_PUBLIC_KEY_SIZE 800\n"
        "#define TEB_SERVER_MLDSA44_PUBLIC_KEY_SIZE 1312\n"
        "#define TEB_SERVER_MLDSA44_SECRET_KEY_SIZE 2560\n\n"
        "/* Deterministic lab-only keys for reproducible ESP32 benchmarks. */\n"
        + format_array(
            "teb_mlkem512_public_key",
            bytes(kem_public),
            "TEB_SERVER_MLKEM512_PUBLIC_KEY_SIZE",
        )
        + "\n"
        + format_array(
            "teb_mldsa44_public_key",
            bytes(mldsa_public),
            "TEB_SERVER_MLDSA44_PUBLIC_KEY_SIZE",
        )
        + "\n"
        + format_array(
            "teb_mldsa44_secret_key",
            bytes(mldsa_secret),
            "TEB_SERVER_MLDSA44_SECRET_KEY_SIZE",
        )
        + "\n#endif /* TEB_PQ_SERVER_KEYS_H */\n"
    )

    (PROJECT_ROOT / "include" / "teb_pq_client_keys.h").write_text(client_header)
    (PROJECT_ROOT / "server" / "teb_pq_server_keys.h").write_text(server_header)
    print("Generated include/teb_pq_client_keys.h")
    print("Generated server/teb_pq_server_keys.h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
