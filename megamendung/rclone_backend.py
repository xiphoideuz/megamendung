"""Thin wrapper around the ``rclone`` CLI.

megamendung provisions one rclone ``mega`` remote per managed account in a
dedicated rclone config file (``~/.config/megamendung/rclone.conf``) so your
main ``~/.config/rclone/rclone.conf`` is left untouched. All storage
operations are delegated to rclone.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .paths import default_rclone_config_path


class RcloneError(Exception):
    """Raised when a rclone invocation fails."""


class Rclone:
    def __init__(
        self,
        binary: str = "rclone",
        config_path: Path | None = None,
    ) -> None:
        self.binary = binary
        self.config_path = Path(config_path or default_rclone_config_path())

    # -- low level --------------------------------------------------------

    def _cmd(self, args: list[str], with_config: bool = True) -> list[str]:
        base = [self.binary]
        if with_config:
            base += ["--config", str(self.config_path)]
        return base + args

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        text: bool = True,
        with_config: bool = True,
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            self._cmd(args, with_config),
            capture_output=True,
            text=text,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip()
            out = (proc.stdout or "").strip()
            raise RcloneError(f"rclone {' '.join(args)} failed ({proc.returncode}): {err or out}")
        return proc

    @staticmethod
    def remote_and_path(remote: str, remote_path: str | None) -> str:
        """``(remote, path) -> 'remote:path'`` with sane path normalisation."""
        path = (remote_path or "").strip()
        if path and not path.startswith("/"):
            path = "/" + path
        if path == "/":
            return f"{remote}:"
        return f"{remote}:{path}"

    # -- config management -------------------------------------------------

    def version(self) -> str:
        proc = self.run(["version"], timeout=30)
        return (proc.stdout or proc.stderr).strip().splitlines()[0]

    def config_create(self, name: str, *, user: str, pass_: str) -> None:
        self.run(
            ["config", "create", name, "mega", f"user={user}", f"pass={pass_}"],
            timeout=60,
        )

    def config_update(self, name: str, *, user: str, pass_: str) -> None:
        self.run(
            ["config", "update", name, f"user={user}", f"pass={pass_}"],
            timeout=60,
        )

    def config_delete(self, name: str) -> None:
        self.run(["config", "delete", name], timeout=60, check=False)

    def listremotes(self) -> list[str]:
        proc = self.run(["listremotes"], timeout=30)
        out = (proc.stdout or "").strip()
        if not out:
            return []
        return [line.strip().rstrip(":") for line in out.splitlines() if line.strip()]

    def obscure(self, secret: str) -> str:
        proc = self.run(["obscure", secret], with_config=False, timeout=30)
        return (proc.stdout or "").strip()

    def reveal(self, obscured: str) -> str:
        proc = self.run(["reveal", obscured], with_config=False, timeout=30)
        return (proc.stdout or "").strip()

    # -- storage operations --------------------------------------------------

    def about(self, remote: str, remote_path: str | None = None, timeout: float = 30.0) -> dict:
        target = self.remote_and_path(remote, remote_path)
        proc = self.run(["about", "--json", target], timeout=timeout, check=False)
        if proc.returncode == 0 and proc.stdout:
            try:
                data = json.loads(proc.stdout)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        if proc.returncode == 0 and proc.stderr:
            try:
                data = json.loads(proc.stderr)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        if proc.returncode != 0:
            raise RcloneError(f"rclone about failed: {proc.stderr.strip()}")
        return {}

    def list(self, remote: str, remote_path: str | None = None, *, recursive: bool = False, files_only: bool = False, dirs_only: bool = False, timeout: float = 60.0) -> list[dict]:
        target = self.remote_and_path(remote, remote_path)
        cmd = ["lsjson", "--no-mimetype"]
        if recursive:
            cmd.append("--recursive")
        if files_only:
            cmd.append("--files-only")
        if dirs_only:
            cmd.append("--dirs-only")
        cmd.append(target)
        proc = self.run(cmd, timeout=timeout)
        return json.loads(proc.stdout or "[]")

    def mkdir(self, remote: str, remote_path: str) -> None:
        self.run(["mkdir", self.remote_and_path(remote, remote_path)], timeout=60)

    def delete(self, remote: str, remote_path: str) -> None:
        self.run(["delete", self.remote_and_path(remote, remote_path)], timeout=300)

    def deletefile(self, remote: str, remote_path: str) -> None:
        self.run(["deletefile", self.remote_and_path(remote, remote_path)], timeout=300)

    def purge(self, remote: str, remote_path: str) -> None:
        self.run(["purge", self.remote_and_path(remote, remote_path)], timeout=300)

    def copy(self, source: str, dest: str, *, timeout: float | None = 600.0) -> None:
        self.run(["copy", source, dest], timeout=timeout)

    def copyto(self, source: str, dest: str, *, timeout: float | None = 600.0) -> None:
        self.run(["copyto", source, dest], timeout=timeout)

    def sync(self, source: str, dest: str, *, timeout: float | None = 3600.0) -> None:
        self.run(["sync", source, dest], timeout=timeout)

    def bisync(self, source: str, dest: str, *, timeout: float | None = 3600.0) -> None:
        proc = subprocess.run(
            [self.binary, "--config", str(self.config_path), "bisync",
             "--resilient", "--recover", source, dest],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RcloneError(f"rclone bisync failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")

    def healthcheck(self, remote: str, timeout: float = 30.0) -> str:
        """Login + liveness probe. Raises RcloneError on failure."""
        proc = self.run(["about", "--json", f"{remote}:"], timeout=timeout, check=False)
        if proc.returncode != 0:
            raise RcloneError(
                f"login/liveness probe {remote}: failed ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        return f"{remote}:"


def sanitize_remote_name(name: str) -> str:
    """Rclone remote section names allow [A-Za-z0-9_]. Keep dots? no."""
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    return cleaned.strip("_") or "account"