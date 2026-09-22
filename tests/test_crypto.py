"""Verify the Python MEGA crypto port against independent C reference vectors."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from megamendung import crypto

VECTORS = json.loads((Path(__file__).parent / "vectors.json").read_text())


def test_b64_url_roundtrip():
    for n in range(1, 40):
        data = bytes(range(n))
        assert crypto.b64_url_encode(data) == base64.b64encode(data).replace(b"/", b"_").replace(b"+", b"-").rstrip(b"=").decode("ascii")
        assert crypto.b64_url_decode(crypto.b64_url_encode(data)) == data


def test_password_key_vector_1():
    pkey = crypto.make_password_key("Tr0ub4dor&3")
    assert pkey.hex() == VECTORS["password_key_hex"]["Tr0ub4dor&3"]


def test_password_key_vector_2():
    pkey = crypto.make_password_key("a")
    assert pkey.hex() == VECTORS["password_key_hex"]["a"]


def test_username_hash_vector():
    pkey = crypto.make_password_key("Tr0ub4dor&3")
    low = crypto.make_username_hash("megamendung.test+mega1@gmail.com", pkey)
    upper = crypto.make_username_hash("megamendung.test+mega1@GMAIL.COM", pkey)
    assert low == VECTORS["username_hash_b64url"]["megamendung.test+mega1@gmail.com"]
    assert upper == VECTORS["username_hash_b64url"]["megamendung.test+mega1@GMAIL.COM"]
    assert low != upper  # hash is content sensitive; caller lowercases for login


def test_master_key_encrypt_vector():
    pkey = crypto.make_password_key("Tr0ub4dor&3")
    master = b"\xab" * 16
    assert crypto.master_key_encrypt(master, pkey) == VECTORS[
        "master_key_encrypted_under_pwkey_1"
    ]


def test_challenge_block_encrypt_vector():
    pkey = crypto.make_password_key("Tr0ub4dor&3")
    cdata = b"\x11" * 32
    assert crypto.b64_url_encode(crypto.aes_ecb_encrypt(pkey, cdata)) == VECTORS[
        "challenge_block_encrypted_under_pwkey_1"
    ]


def test_master_key_roundtrip():
    pkey = crypto.make_password_key("secret123")
    master = b"\xde\xad\xbe\xef" * 4
    enc = crypto.master_key_encrypt(master, pkey)
    assert crypto.master_key_decrypt(enc, pkey) == master


def test_mpi_and_rsa_pubk_structure():
    pubk_b64, privk_plain = crypto.rsa_keygen()
    raw = pubk_b64  # base64url pubk
    assert isinstance(raw, str) and raw
    assert b"\x00\x00" not in privk_plain[:2]
    assert len(privk_plain) in (648, 646)  # 2x(bitlen+2) for 1024-bit halves etc.


def test_privk_encrypt_decrypt_roundtrip():
    import secrets

    _, privk_plain = crypto.rsa_keygen()
    master = secrets.token_bytes(16)
    enc = crypto.privk_encrypt(master, privk_plain)
    dec = crypto.master_key_decrypt(enc, master)
    assert dec == privk_plain.ljust((16 - len(privk_plain) % 16) % 16 + len(privk_plain), b"\x00")


def test_make_request_id():
    assert len(crypto.make_request_id()) == 10
    assert crypto.make_request_id() != crypto.make_request_id()