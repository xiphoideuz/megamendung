"""``megamendung connect`` - the WhatsApp-Web-style "device" agent.

The web GUI lives in a Cloudflare Worker (``web/`` in this repo) and only
relays messages; all real storage work happens here, where rclone and ffmpeg
run. Like a phone in WhatsApp Web, this process dials *out* to the worker via
WebSocket (no inbound ports needed), authenticates with the pairing code, and
then waits for JSON-RPC commands from the browser:

* browser -> worker -> agent   ``{"id","method","params"}``
* agent -> worker -> browsers  ``{"type":"reply","id","ok","result|error"}``
* agent broadcasts             ``{"type":"event", ...}`` (job progress etc.)

The agent runs jobs serially against the same Config/rclone each CLI command
would use, so the web GUI and the terminal never fight over rclone state.
"""

from __future__ import annotations

import dataclasses
import json
import queue
import sys
import threading
import time
from pathlib import Path

from . import accounts, manage, refresh
from .config import Config, resolve_remote_prefix
from .pair import PairCode
from .rclone_backend import Rclone, RcloneError

HEARTBEAT_SECONDS = 25.0
RECONNECT_BASE_SECONDS = 3.0
RECONNECT_MAX_SECONDS = 60.0

_HANDLED = (
    accounts.AccountError,
    RcloneError,
    KeyError,
    ValueError,
    OSError,
)


class ConnectError(Exception):
    """User-facing connect problem."""


class Reconnectable(ConnectError):
    """Lost the connection; the loop should retry."""


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def _to_jsonable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# RPC dispatch (single-threaded, mirrors the CLI command surface)
# ---------------------------------------------------------------------------


def _require(params: dict, key: str):
    if key not in params:
        raise ValueError(f"missing parameter: {key}")
    return params[key]


def _build_dispatch(cfg: Config, rclone: Rclone) -> dict[str, callable]:
    def accounts_list(p):
        return accounts.list_accounts(cfg, rclone)

    def accounts_show(p):
        return accounts.show_account(cfg, rclone, name=_require(p, "name"))

    def accounts_remove(p):
        return accounts.remove_account(cfg, rclone, name=_require(p, "name"))

    def accounts_refresh(p):
        result = refresh.refresh_accounts(
            cfg,
            rclone,
            names=p.get("names"),
            sleep=float(p.get("sleep", 2.0)),
            provision=bool(p.get("provision", True)),
        )
        return {
            "ok": result.ok,
            "failed": result.failed,
            "errors": result.errors,
            "success": result.success,
        }

    def accounts_create(p):
        created = accounts.create_accounts(
            cfg,
            rclone,
            count=int(p.get("count", 1)),
            base_name=p.get("base"),
            email=p.get("email"),
            password=p.get("password"),
            display_name=p.get("display_name"),
        )
        return {
            "emails": [a.email for a in created],
            "names": [a.name for a in created],
            "next": "accounts.verify NAME '<#confirm... link from the MEGA email>'",
        }

    def accounts_verify(p):
        account = accounts.verify_account(
            cfg, rclone, name=_require(p, "name"), confirm_link=_require(p, "link")
        )
        return {"name": account.name, "email": account.email}

    def accounts_import(p):
        imported = accounts.import_accounts(
            cfg,
            rclone,
            rclone_conf=Path(p["rclone_conf"]).expanduser()
            if p.get("rclone_conf")
            else None,
            credentials_file=Path(p["credentials_file"]).expanduser()
            if p.get("credentials_file")
            else None,
        )
        return {"imported": len(imported), "names": [a.name for a in imported]}

    def config_show(p):
        return {
            "settings": dict(cfg.settings),
            "accounts": accounts.list_accounts(cfg, rclone),
            "jobs": [dataclasses.asdict(j) for j in cfg.jobs.values()],
        }

    def config_set(p):
        base_email = p.get("base_email")
        base_name = p.get("base_name")
        if base_email:
            cfg.settings["base_email"] = base_email
        if base_name:
            cfg.settings["base_name"] = base_name
        if base_email or base_name:
            cfg.save()
        return {"settings": dict(cfg.settings)}

    def df(p):
        return manage.df(cfg, rclone, p.get("names") or [])

    def ls(p):
        return manage.ls(
            cfg,
            rclone,
            name=_require(p, "name"),
            remote_path=p.get("path") or "/",
            recursive=bool(p.get("recursive", False)),
            files_only=bool(p.get("files", False)),
            dirs_only=bool(p.get("dirs", False)),
        )

    def mkdir(p):
        return manage.mkdir(cfg, rclone, name=_require(p, "name"), remote_path=_require(p, "path"))

    def rm(p):
        return manage.rm(
            cfg,
            rclone,
            name=_require(p, "name"),
            remote_path=_require(p, "path"),
            recursive=bool(p.get("recursive", False)),
        )

    def upload(p):
        return manage.upload(
            cfg,
            rclone,
            name=_require(p, "name"),
            local=_require(p, "local"),
            remote_path=p.get("path"),
        )

    def download(p):
        return manage.download(
            cfg,
            rclone,
            name=_require(p, "name"),
            remote_path=_require(p, "path"),
            local=_require(p, "local"),
        )

    def sync(p):
        return manage.sync_path(
            cfg,
            rclone,
            name=_require(p, "name"),
            local=_require(p, "local"),
            remote_path=_require(p, "path"),
            mode=p.get("mode", "push"),
        )

    def sync_jobs(p):
        return list(manage.run_jobs(cfg, rclone, job_names=p.get("jobs")))

    def sync_add(p):
        from .config import SyncJob

        job = SyncJob(
            name=_require(p, "name"),
            account=_require(p, "account"),
            local=_require(p, "local"),
            remote=_require(p, "remote"),
            mode=p.get("mode", "push"),
        )
        cfg.jobs[job.name] = job
        cfg.save()
        return dataclasses.asdict(job)

    def sync_remove(p):
        name = _require(p, "name")
        try:
            del cfg.jobs[name]
        except KeyError:
            raise KeyError(f"sync job not found: {name}")
        cfg.save()
        return {"removed": name}

    def compress(p):
        from .compress import StateStore, compress_images, compress_videos
        from .paths import default_state_path

        directory = Path(_require(p, "path")).expanduser()
        if not directory.is_dir():
            raise ValueError(f"not a directory: {directory}")
        state = StateStore(Path(p.get("state_path") or default_state_path()))
        result: dict = {}
        if p.get("images", True):
            r = compress_images(
                directory,
                state=state,
                quality=int(p.get("quality", 85)),
                max_dimension=int(p["max_dimension"]) if p.get("max_dimension") else None,
                force=bool(p.get("force", False)),
            )
            result["images"] = _to_jsonable(r)
        if p.get("videos", True):
            r = compress_videos(
                directory,
                state=state,
                preset=p.get("preset", "fast"),
                crf=int(p.get("crf", 23)),
                force=bool(p.get("force", False)),
            )
            result["videos"] = _to_jsonable(r)
        return result

    def system_methods(p):
        return sorted(method for method in TABLE if method != "system.methods")

    TABLE = {
        "accounts.list": accounts_list,
        "accounts.show": accounts_show,
        "accounts.remove": accounts_remove,
        "accounts.refresh": accounts_refresh,
        "accounts.create": accounts_create,
        "accounts.verify": accounts_verify,
        "accounts.import": accounts_import,
        "config.show": config_show,
        "config.set": config_set,
        "df": df,
        "ls": ls,
        "mkdir": mkdir,
        "rm": rm,
        "upload": upload,
        "download": download,
        "sync": sync,
        "sync.jobs": sync_jobs,
        "sync.add": sync_add,
        "sync.remove": sync_remove,
        "compress": compress,
        "system.methods": system_methods,
        "system.ping": lambda p: {"pong": True},
    }
    return TABLE


# ---------------------------------------------------------------------------
# WebSocket glue (sync client from websocket-client)
# ---------------------------------------------------------------------------


def _to_ws_url(url: str, pair_id: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif not (url.startswith("ws://") or url.startswith("wss://")):
        url = "wss://" + url
    return f"{url}/relay/{pair_id}"


class _Agent:
    """One established connection; runs jobs on a serial worker thread."""

    def __init__(self, ws_url: str, code: PairCode, *, heartbeat: float):
        self.ws_url = ws_url
        self.code = code
        self.heartbeat = heartbeat
        self._jobs: queue.Queue = queue.Queue()
        self._running = True
        self._send_lock = threading.Lock()
        self._thread = threading.Thread(target=self._dispatch_loop, name="megamendung-rpc", daemon=True)

    def log_line(self, *parts: str) -> None:
        print(" ".join(parts), file=sys.stderr, flush=True)

    def send(self, frame: dict) -> None:
        payload = json.dumps(frame)
        with self._send_lock:
            self.ws.send(payload)

    # -- heartbeat ----------------------------------------------------------

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(self.heartbeat)
            try:
                self.send({"type": "ping", "t": int(time.time())})
            except Exception:
                return  # socket went away; recv loop will fail and handle it

    def stop(self):
        self._running = False
        self._jobs.put(None)

    # -- dispatch worker ----------------------------------------------------

    def _dispatch_loop(self):
        while True:
            job = self._jobs.get()
            if job is None or not self._running:
                return
            try:
                dispatcher = self.dispatcher
                handler = dispatcher.get(job["method"])
                if handler is None:
                    raise ValueError(f"unknown method: {job['method']}")
                self.send({"type": "event", "id": job["id"], "stage": "start", "method": job["method"]})
                result = handler(job.get("params") or {})
                self.send({"type": "reply", "id": job["id"], "ok": True, "result": _to_jsonable(result)})
            except _HANDLED as exc:
                self.send({"type": "reply", "id": job["id"], "ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - report and keep the loop alive
                self.log_line(f"rpc failed: {job.get('method')}: {exc!r}")
                try:
                    self.send({"type": "reply", "id": job["id"], "ok": False, "error": str(exc)})
                except Exception:
                    return

    # -- main recv loop -----------------------------------------------------

    def run(self) -> int:
        from websocket import WebSocketConnectionClosedException

        self.ws = None
        import websocket as _ws

        self.ws = _ws.create_connection(self.ws_url, timeout=120)
        self.send(
            {"type": "hello", "role": "agent", "code": self.code.code, "name": _hostname()}
        )
        while True:
            raw = self.ws.recv()
            if not raw:
                raise Reconnectable("connection closed")
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ftype = frame.get("type")
            if ftype == "hello" and frame.get("reply"):
                self.log_line("paired: connected to relay (device online)")
                cfg = _load_config()
                rclone = _load_rclone(resolve_remote_prefix(cfg))
                self.dispatcher = _build_dispatch(cfg, rclone)
                threading.Thread(target=self._heartbeat_loop, name="megamendung-ping", daemon=True).start()
                self._thread.start()
                continue
            if ftype == "ping":
                self.send({"type": "pong", "t": frame.get("t")})
                continue
            if ftype == "pong":
                continue
            if ftype == "event":
                if frame.get("name") == "client_joined":
                    self.log_line("a browser client joined the session")
                continue
            if "method" in frame:
                self._jobs.put(frame)
                continue
            if ftype == "shutdown":
                self.log_line("shutdown requested by worker")
                self.stop()
                return 0
        # unreachable - recv raises on disconnect


def _hostname() -> str:
    try:
        import socket

        return socket.gethostname()
    except Exception:
        return "megamendung"


def _load_config():
    from .paths import default_config_path

    return Config.load(default_config_path())


def _load_rclone(prefix: str = ""):
    from .paths import default_rclone_config_path

    return Rclone(config_path=default_rclone_config_path(), prefix=prefix)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_connect(
    url: str,
    code: str,
    *,
    reconnect: bool = True,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> int:
    """Connect and serve commands until the process is asked to stop."""
    from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException

    pair = PairCode.parse(code)
    ws_url = _to_ws_url(url, pair.pair_id)
    attempt = 0
    print(f"megamendung connect: {ws_url}", file=sys.stderr, flush=True)
    while True:
        agent = _Agent(ws_url, pair, heartbeat=heartbeat)
        try:
            return agent.run()
        except KeyboardInterrupt:
            agent.stop()
            print("\nmegamendung connect: stopped", file=sys.stderr, flush=True)
            return 0
        except (WebSocketConnectionClosedException, WebSocketTimeoutException, Reconnectable, OSError) as exc:
            agent.stop()
            if not reconnect:
                raise ConnectError(str(exc))
            delay = min(RECONNECT_BASE_SECONDS * (2 ** attempt), RECONNECT_MAX_SECONDS)
            attempt += 1
            print(
                f"megamendung connect: connection lost ({exc}); retrying in {delay:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
            continue