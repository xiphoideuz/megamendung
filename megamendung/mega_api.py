"""Minimal MEGA (mega.nz) REST API client used for account registration.

Implements the two-step signup flow that megatools' ``megareg`` performs:

1. ``register()``  - create a non-verified account, returns a ``RegState``
   that must be kept alongside the credentials.
2. ``verify()``    - finish signup with the confirmation link (including the
   signup key) that MEGA emails to the address.

Storage operations are NOT performed here; production commands shell out to
``rclone`` (which has a first-class ``mega`` backend) in ``rclone_backend.py``.
"""

from __future__ import annotations

import json
import time
import urllib.parse

import requests

from . import crypto
from .crypto import CryptoError

API_BASE = "https://g.api.mega.co.nz/cs"
USER_AGENT = "MEGAsync/4.11.2.0"

# MEGA API error codes (subset of megatools' SRV_* plus mega errors)
ERROR_CODES = {
    -1: "internal error",
    -2: "arguments error",
    -3: "request failed (transient), retry",
    -4: "rate limit exceeded",
    -5: "failed permanently",
    -6: "too many concurrent connections",
    -7: "out of range",
    -8: "expired",
    -9: "not found",
    -10: "circular linkage",
    -11: "access denied",
    -12: "already exists",
    -13: "incomplete",
    -14: "cryptographic error",
    -15: "bad session id",
    -16: "blocked",
    -17: "over quota",
    -18: "temporarily unavailable",
    -19: "too many connections",
}


class MegaApiError(Exception):
    """Raised when the MEGA API returns an error or transport fails."""


class RegState:
    """Opaque state produced by ``register()`` and needed by ``verify()``."""

    __slots__ = ("user_handle", "password_key", "challenge", "email")

    def __init__(self, user_handle: str, password_key: bytes, challenge: bytes, email: str):
        self.user_handle = user_handle
        self.password_key = password_key
        self.challenge = challenge
        self.email = email

    def serialize(self) -> str:
        """Encode as ``<b64 pk>:<b64 challenge>:<user_handle>`` (megareg format)."""
        import base64

        pk = base64.b64encode(self.password_key).decode("ascii")
        ch = base64.b64encode(self.challenge).decode("ascii")
        return f"{pk}:{ch}:{self.user_handle}"

    @classmethod
    def deserialize(cls, value: str, email: str) -> "RegState":
        import base64
        import re

        m = re.match(r"^([A-Za-z0-9/+=]{24}):([A-Za-z0-9/+=]{24}):(.+)$", value)
        if not m:
            raise MegaApiError("invalid registration state")
        pk = base64.b64decode(m.group(1))
        ch = base64.b64decode(m.group(2))
        if len(pk) != 16 or len(ch) != 16:
            raise MegaApiError("invalid registration state")
        return cls(m.group(3), pk, ch, email)


class MegaApi:
    def __init__(self, timeout: float = 60.0, max_retries: int = 3):
        self._timeout = timeout
        self._max_retries = max_retries
        self._sid: str | None = None
        self._seq = 0
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    # -- low level -------------------------------------------------------

    def _post(self, body: str, sid: bool) -> object:
        req_id = crypto.make_request_id()
        url = f"{API_BASE}?id={req_id}"
        if sid and self._sid:
            url += f"&sid={urllib.parse.quote(self._sid, safe='-_')}"
        resp = self._session.post(
            url, data=body, timeout=self._timeout,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    def call(self, request: list, sid: bool = True) -> object:
        body = json.dumps(request, separators=(",", ":"))
        delay = 0.25
        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                data = self._post(body, sid)
            except (requests.RequestException, ValueError) as exc:  # non-JSON / transport
                if attempt >= self._max_retries:
                    raise MegaApiError(f"API transport error: {exc}") from exc
                last_err = exc
                time.sleep(delay)
                delay *= 2
                continue
            if (
                isinstance(data, list)
                and len(data) == 1
                and isinstance(data[0], int)
                and data[0] == 0
            ):
                return data[0]
            if isinstance(data, list) and len(data) == 1 and isinstance(data[0], int):
                code = data[0]
                if code == -3:
                    time.sleep(delay)
                    delay *= 2
                    continue  # EAGAIN -> retry
                raise MegaApiError(f"MEGA API error {code}: {ERROR_CODES.get(code, 'unknown')}")
            if isinstance(data, list) and len(data) == 1:
                return data[0]
            if isinstance(data, list):
                return data
            return data
        raise MegaApiError(f"API call failed after retries: {last_err}")

    # -- registration flow ------------------------------------------------

    def register(self, email: str, password: str, name: str) -> RegState:
        """Create a non-verified account. Returns state to pass to ``verify``."""
        self._sid = None
        email_lower = email.lower()
        password_key = crypto.make_password_key(password)
        master_key = secrets_bytes(16)
        username_hash = crypto.make_username_hash(email_lower, password_key)

        ssc = secrets_bytes(16)
        ts = ssc + crypto.aes_ecb_encrypt_block(master_key, ssc)

        # 1. create anonymous user carrying the master key
        handle = self._call_str(
            [{"a": "up", "k": crypto.master_key_encrypt(master_key, password_key),
              "ts": crypto.b64_url_encode(ts)}],
            sid=False,
        )
        self._seq += 1

        # 2. login anonymously
        self._sid = self._login_ansi_user_handle(handle)  # type: ignore[assignment]
        self._seq += 1

        # 3. get user info
        self.call([{"a": "ug"}])

        # 4. set display name
        self.call([{"a": "up", "name": name}])

        # 5. request confirmation email (a:uc) -> returns [0]
        challenge = secrets_bytes(4) + b"\x00" * 8 + secrets_bytes(4)
        c_data = master_key + challenge
        self.call(
            [{
                "a": "uc",
                "c": crypto.b64_url_encode(crypto.aes_ecb_encrypt(password_key, c_data)),
                "n": crypto.b64_url_encode(name.encode("utf-8")),
                "m": crypto.b64_url_encode(email.encode("utf-8")),
            }],
        )
        self._sid = None

        return RegState(handle, password_key, challenge, email)

    def verify(self, state: RegState, confirm_link: str) -> None:
        """Complete registration using the URL from MEGA's confirmation email."""
        signup_key = extract_signup_key(confirm_link)
        if state.user_handle:
            self._sid = self._login_ansi_user_handle(state.user_handle)
        else:
            self._sid = None
            raise MegaApiError("registration state has no user handle")

        # 'ud' returns [email, name, handle, enc(master_key), enc(challenge)]
        elements = self.call([{"a": "ud", "c": signup_key}])
        if not isinstance(elements, list) or len(elements) != 5:
            raise MegaApiError(f"unexpected response from 'ud': {elements!r}")
        email_b64, _name_b64, _handle, k_b64, ch_b64 = elements
        email = crypto.b64_url_decode(str(email_b64)).decode()
        master_key = crypto.master_key_decrypt(str(k_b64), state.password_key)
        challenge = crypto.master_key_decrypt(str(ch_b64), state.password_key)
        if challenge != state.challenge:
            raise MegaApiError("challenge mismatch - confirmation link does not match account")

        username_hash = crypto.make_username_hash(email.lower(), state.password_key)
        self._sid = None
        self.call([{"a": "up", "c": signup_key, "uh": username_hash}])
        self._sid = self._login_email(  # type: ignore[assignment]
            email.lower(), username_hash
        )

        pubk_b64, privk_plain = crypto.rsa_keygen()
        privk_b64 = crypto.privk_encrypt(master_key, privk_plain)
        self.call([{"a": "up", "pubk": pubk_b64, "privk": privk_b64}])
        self._sid = None

    # -- helpers -----------------------------------------------------------

    def _call_str(self, request: list, sid: bool) -> str:
        result = self.call(request, sid=sid)
        if not isinstance(result, str):
            raise MegaApiError(f"expected string response, got {result!r}")
        return result

    def _login_ansi_user_handle(self, handle: str) -> str | None:
        node = self.call([{"a": "us", "user": handle}], sid=False)
        return _member_string(node, "tsid")

    def _login_email(self, email: str, username_hash: str) -> str | None:
        node = self.call(
            [{"a": "us", "user": email, "uh": username_hash}], sid=False
        )
        return _member_string(node, "tsid")


def _member_string(node: object, key: str) -> str | None:
    if isinstance(node, dict) and isinstance(node.get(key), str):
        return node[key]
    return None


def extract_signup_key(link: str) -> str:
    """Pull the 80-150 char signup key out of a MEGA confirmation URL."""
    import re

    m = re.search(r"#confirm([A-Za-z0-9_-]{80,150})", link)
    if m:
        return m.group(1)
    raise MegaApiError(f"invalid confirmation link: {link!r}")


def secrets_bytes(n: int) -> bytes:
    import secrets

    return secrets.token_bytes(n)