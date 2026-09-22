"""Tests for ``megamendung pair`` and ``megamendung connect``.

The worker side of the WhatsApp-Web-style relay is simulated in-process with
a tiny RFC 6455 WebSocket server (no extra dependencies) so the agent's
handshake, RPC dispatch and reply/event framing are verified end to end.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time

import pytest

from megamendung.connect import ConnectError, run_connect
from megamendung.pair import PairCode, make_pair_code

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _encode_text(data: str) -> bytes:
    payload = data.encode("utf-8")
    n = len(payload)
    if n < 126:
        header = bytes([0x81, n])
    elif n < 65536:
        header = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", n)
    return header + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    b1, b2 = _recv_exact(sock, 2)
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    masked = bool(b2 & 0x80)
    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else None
    payload = bytearray(_recv_exact(sock, length))
    if mask:
        for i in range(length):
            payload[i] ^= mask[i % 4]
    return opcode, bytes(payload)


class WSConn:
    """One server-side WebSocket connection."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.lock = threading.Lock()

    def send_text(self, data: str) -> None:
        with self.lock:
            self.sock.sendall(_encode_text(data))

    def recv_text(self) -> str:
        while True:
            opcode, payload = _recv_frame(self.sock)
            if opcode == 0x8:  # close
                self.sock.sendall(_encode_text(""))
                raise EOFError("peer closed")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if opcode == 0xA:  # pong
                continue
            return payload.decode("utf-8")

    def recv_json(self) -> dict:
        return json.loads(self.recv_text())

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class FakeRelay:
    """A stand-in for the Worker relay (pair room per pair_id).

    The handler thread only *reads* frames into an inbox; the test drives the
    agent by calling :meth:`send` and consuming inbox frames with
    :meth:`wait_for`.
    """

    def __init__(self, expected_code: str):
        self.expected_code = expected_code
        self.conn: WSConn | None = None
        self.ready = threading.Event()
        self.handshake_ok = False
        self.inbox: list[dict] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.url = f"http://127.0.0.1:{self._srv.getsockname()[1]}"
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        try:
            self._srv.close()
        except OSError:
            pass

    def _accept_loop(self):
        while True:
            try:
                sock, _ = self._srv.accept()
            except Exception:
                return
            threading.Thread(target=self._handle, args=(sock,), daemon=True).start()

    def _handle(self, sock: socket.socket):
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += sock.recv(4096)
            headers = request.decode("latin1")
            key = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if not key:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return
            accept = base64.b64encode(
                hashlib.sha1((key + GUID).encode()).digest()
            ).decode()
            sock.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
            conn = WSConn(sock)
            hello = conn.recv_json()
            if hello.get("type") != "hello" or hello.get("code") != self.expected_code:
                conn.close()
                return
            with self._lock:
                self.conn = conn
            self.handshake_ok = True
            conn.send_text(
                json.dumps(
                    {"type": "hello", "reply": True, "you": "agent", "pair_id": hello["code"].split(":")[0]}
                )
            )
            self.ready.set()
            while True:
                frame = conn.recv_json()
                if not frame or frame.get("type") == "shutdown":
                    return
                with self._lock:
                    self.inbox.append(frame)
        except Exception:
            return
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def send(self, frame: dict) -> None:
        assert self.conn is not None, "agent not connected"
        self.conn.send_text(json.dumps(frame))

    def wait_for(self, predicate, timeout: float = 10.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for i, frame in enumerate(self.inbox):
                    if predicate(frame):
                        return self.inbox.pop(i)
            time.sleep(0.01)
        return None


def run_agent(url: str, code: str, result_holder: list) -> None:
    try:
        result_holder.append(run_connect(url, code, reconnect=False))
    except BaseException as exc:  # noqa: BLE001
        result_holder.append(exc)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("MEGAMENDUNG_CONFIG_DIR", str(tmp_path))
    return tmp_path


# --- pair ----------------------------------------------------------------


def test_pair_code_parse_roundtrip():
    pair = make_pair_code()
    assert len(pair.pair_id) == 8
    assert pair.secret and ":" not in pair.secret
    assert pair.code == f"{pair.pair_id}:{pair.secret}"
    assert PairCode.parse(pair.code) == pair


def test_pair_code_rejects_garbage():
    with pytest.raises(ValueError):
        PairCode.parse("nonsense")


# --- connect --------------------------------------------------------------


def test_connect_handshake_and_ping(isolated_config):
    pair = make_pair_code()
    relay = FakeRelay(pair.code)
    starter = relay.start()
    results: list = []
    thread = threading.Thread(target=run_agent, args=(starter, pair.code, results), daemon=True)
    thread.start()
    assert relay.ready.wait(timeout=10), "agent never reached the relay"
    assert relay.handshake_ok

    relay.send({"id": "1", "method": "system.ping", "params": {}})
    reply = relay.wait_for(lambda f: f.get("id") == "1" and f.get("type") == "reply")
    assert reply == {"type": "reply", "id": "1", "ok": True, "result": {"pong": True}}

    relay.send({"type": "shutdown"})
    thread.join(timeout=10)
    assert results
    assert results[0] == 0  # clean exit


def test_connect_unknown_method_error(isolated_config):
    pair = make_pair_code()
    relay = FakeRelay(pair.code)
    starter = relay.start()
    results: list = []
    thread = threading.Thread(target=run_agent, args=(starter, pair.code, results), daemon=True)
    thread.start()
    assert relay.ready.wait(timeout=10)

    relay.send({"id": "2", "method": "nope.nope", "params": {}})
    reply = relay.wait_for(lambda f: f.get("id") == "2" and f.get("type") == "reply")
    assert reply["ok"] is False
    assert "unknown method" in reply["error"]

    relay.send({"type": "shutdown"})
    thread.join(timeout=10)


def test_connect_config_show(isolated_config, tmp_path):
    (tmp_path / "megamendung.conf").write_text("[settings]\nbase_email = me@example.com\n")
    pair = make_pair_code()
    relay = FakeRelay(pair.code)
    starter = relay.start()
    results: list = []
    thread = threading.Thread(target=run_agent, args=(starter, pair.code, results), daemon=True)
    thread.start()
    assert relay.ready.wait(timeout=10)

    relay.send({"id": "3", "method": "config.show", "params": {}})
    reply = relay.wait_for(lambda f: f.get("id") == "3" and f.get("type") == "reply")
    assert reply["ok"] is True
    assert reply["result"]["settings"]["base_email"] == "me@example.com"

    relay.send({"type": "shutdown"})
    thread.join(timeout=10)


def test_connect_rejects_bad_code(isolated_config):
    relay = FakeRelay("aaaa1111:right-secret")
    starter = relay.start()
    results: list = []
    thread = threading.Thread(
        target=run_agent,
        args=(starter, "bbbb2222:wrong-secret", results),
        daemon=True,
    )
    thread.start()
    assert not relay.ready.wait(timeout=2), "server should reject a mismatched code"
    thread.join(timeout=10)
    assert results and isinstance(results[0], ConnectError)