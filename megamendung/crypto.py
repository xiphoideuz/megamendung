"""MEGA client-side cryptography primitives.

Ported 1:1 from the reference implementation in megatools
(``tools/libtools/oldmega.c``, GPL-2.0) so that accounts registered by
megamendung are compatible with the official MEGA clients and rclone.

The verification vectors in ``tests/vectors.json`` were produced by compiling
that exact C algorithm (see ``tests/c_harness.c``), so a regression here is
caught without touching the live MEGA API.
"""

from __future__ import annotations

import base64
import secrets
import string

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CryptoError(Exception):
    """Raised when a MEGA crypto operation fails."""


def b64_url_encode(data: bytes) -> str:
    """base64 with URL-safe alphabet and no padding (MEGA ``base64urlencode``)."""
    return base64.b64encode(data, altchars=b"-_").rstrip(b"=").decode("ascii")


def b64_url_decode(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("expected string")
    v = value.encode("ascii")
    v = v.replace(b"-", b"+").replace(b"_", b"/")
    v += b"=" * ((4 - len(v) % 4) % 4)
    return base64.b64decode(v)


def aes_ecb_encrypt_block(key: bytes, block: bytes) -> bytes:
    """Single-block AES-128-ECB encrypt (blocks are always 16 bytes here)."""
    if len(key) != 16:
        raise CryptoError(f"invalid AES key length {len(key)}")
    if len(block) != 16:
        raise CryptoError(f"invalid AES block length {len(block)}")
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    out = enc.update(block) + enc.finalize()
    return out


def aes_ecb_decrypt_block(key: bytes, block: bytes) -> bytes:
    if len(key) != 16 or len(block) != 16:
        raise CryptoError("invalid AES key/block length")
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(block) + dec.finalize()


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """AES-128-ECB over possibly-multi-block ``data`` (must be 16-aligned)."""
    if len(data) % 16 != 0:
        raise CryptoError("AES-ECB input must be a multiple of 16 bytes")
    out = bytearray()
    for off in range(0, len(data), 16):
        out += aes_ecb_encrypt_block(key, data[off : off + 16])
    return bytes(out)


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if len(data) % 16 != 0:
        raise CryptoError("AES-ECB input must be a multiple of 16 bytes")
    out = bytearray()
    for off in range(0, len(data), 16):
        out += aes_ecb_decrypt_block(key, data[off : off + 16])
    return bytes(out)


def make_password_key(password: str) -> bytes:
    """Derive the 16-byte ``password_key`` from a password.

    Matches megatools' ``make_password_key``: 65536 rounds, password chunked
    into 16-byte AES keys.
    """
    pw = password.encode("utf-8")
    pkey = bytes(
        [0x93, 0xC4, 0x67, 0xE3, 0x7D, 0xB0, 0xC7, 0xA4, 0xD1, 0xBE, 0x3F, 0x81,
         0x01, 0x52, 0xCB, 0x56]
    )
    for _ in range(65536):
        for i in range(0, len(pw), 16):
            chunk = pw[i : i + 16].ljust(16, b"\x00")
            pkey = aes_ecb_encrypt_block(chunk, pkey)
    return pkey


def make_username_hash(email: str, password_key: bytes) -> str:
    """``uh`` used everywhere a username hash is needed.

    ``email`` should already be lower-cased (MEGA treats usernames as
    case-insensitive).
    """
    un = email.encode("utf-8")
    h = bytearray(16)
    for i, byte in enumerate(un):
        h[i % 16] ^= byte
    for _ in range(16384):
        h = bytearray(aes_ecb_encrypt_block(password_key, bytes(h)))
    digest = bytes(h)
    return b64_url_encode(digest[0:4] + digest[8:12])


def master_key_encrypt(master_key: bytes, password_key: bytes) -> str:
    """``k = AES-ECB(master_key) under password_key``, URL-base64."""
    return b64_url_encode(aes_ecb_encrypt(password_key, master_key))


def master_key_decrypt(value: str, password_key: bytes) -> bytes:
    return aes_ecb_decrypt(password_key, b64_url_decode(value))


def make_request_id() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _mpi_bytes(n: int) -> bytes:
    """OpenSSL-style MPI: 2-byte big-endian bit length + big-endian payload."""
    bits = n.bit_length()
    size = (bits + 7) // 8
    return bits.to_bytes(2, "big") + n.to_bytes(size, "big")


def rsa_keygen(key_size: int = 2048, public_exponent: int = 3):
    """Generate the RSA key pair MEGA requires on signup verification.

    Returns ``(pubk_b64, privk_plain)`` where ``privk_plain`` is the
    concatenated MPI p||q||d||u that MEGA stores encrypted under the master
    key.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(
        public_exponent=public_exponent, key_size=key_size
    )
    numbers = private.private_numbers()
    p, q, d = numbers.p, numbers.q, numbers.d
    iqmp = numbers.iqmp  # q^-1 mod p  == megatools' ``u``

    # megatools emits them in the order (p, q, d, u) where its p/q are the
    # swapped OpenSSL primes; iqmp is defined over that same pairing.
    privk_plain = _mpi_bytes(q) + _mpi_bytes(p) + _mpi_bytes(d) + _mpi_bytes(iqmp)
    pubk_plain = _mpi_bytes(private.public_key().public_numbers().n) + _mpi_bytes(
        private.public_key().public_numbers().e
    )
    return b64_url_encode(pubk_plain), privk_plain


def privk_encrypt(master_key: bytes, privk_plain: bytes) -> str:
    """Pad ``privk_plain`` to a 16-byte multiple and encrypt under master key."""
    pad = (16 - len(privk_plain) % 16) % 16
    if pad:
        privk_plain = privk_plain + b"\x00" * pad
    return b64_url_encode(aes_ecb_encrypt(master_key, privk_plain))