"""Device pairing for the web GUI.

`megamendung pair` generates a short pairing code for the WhatsApp-Web-style
"connect a device" flow. The code has two halves:

    <pair_id>:<secret>

The pair id selects the relay channel (a single Durable Object is named after
it) and the secret is the shared credential. The machine presents it when it
runs ``megamendung connect``; the web GUI asks for the same code. Neither half
is ever stored: trust-on-first-use means whoever first presents a valid code
on the channel binds to it.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PairCode:
    pair_id: str
    secret: str

    @property
    def code(self) -> str:
        return f"{self.pair_id}:{self.secret}"

    @classmethod
    def parse(cls, code: str) -> "PairCode":
        pair_id, _, secret = code.strip().partition(":")
        pair_id = pair_id.strip()
        secret = secret.strip()
        if not pair_id or not secret:
            raise ValueError(
                "invalid pairing code; expected <pair_id>:<secret> as printed "
                "by `megamendung pair`"
            )
        return cls(pair_id, secret)


def make_pair_code() -> PairCode:
    pair_id = secrets.token_hex(4)
    secret = base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode("ascii")
    return PairCode(pair_id, secret)