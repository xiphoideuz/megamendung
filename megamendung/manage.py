"""Storage operations per account (ported from the legacy mega_manager tools).

All heavy lifting is delegated to rclone's ``mega`` backend; these helpers
normalise arguments and add the account-oriented CLI surface.
"""

from __future__ import annotations

import os

from .config import Config
from .rclone_backend import Rclone, RcloneError, sanitize_remote_name


def human_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def df(cfg: Config, rclone: Rclone, names: list[str]) -> list[dict]:
    targets = [cfg.account(n) for n in names] if names else list(cfg.accounts.values())
    rows: list[dict] = []
    for account in targets:
        remote = sanitize_remote_name(account.name)
        usage = rclone.about(remote)
        rows.append(
            {
                "name": account.name,
                "email": account.email,
                "verified": account.verified,
                "total": usage.get("total"),
                "used": usage.get("used"),
                "free": usage.get("free"),
                "trashed": usage.get("trashed"),
                "other": usage.get("other"),
            }
        )
    return rows


def ls(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    remote_path: str,
    recursive: bool = False,
    files_only: bool = False,
    dirs_only: bool = False,
) -> list[dict]:
    account = cfg.account(name)
    remote = sanitize_remote_name(account.name)
    return rclone.list(
        remote,
        remote_path,
        recursive=recursive,
        files_only=files_only,
        dirs_only=dirs_only,
    )


def mkdir(cfg: Config, rclone: Rclone, *, name: str, remote_path: str) -> None:
    account = cfg.account(name)
    rclone.mkdir(sanitize_remote_name(account.name), remote_path)


def rm(cfg: Config, rclone: Rclone, *, name: str, remote_path: str, recursive: bool = False) -> None:
    account = cfg.account(name)
    remote = sanitize_remote_name(account.name)
    if recursive:
        rclone.purge(remote, remote_path)
        return
    try:
        rclone.deletefile(remote, remote_path)
    except RcloneError as exc:
        if "directory" in str(exc).lower() or "is a directory" in str(exc).lower():
            raise ValueError(
                f"{remote_path} is a directory; pass -r/--recursive to remove it"
            ) from exc
        raise


def _normalise_remote_target(remote_path: str) -> str:
    path = remote_path.strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def upload(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    local: str,
    remote_path: str,
) -> None:
    account = cfg.account(name)
    remote = sanitize_remote_name(account.name)
    target = remote + ":"
    stripped = _normalise_remote_target(remote_path).strip("/")
    if stripped:
        target = f"{remote}:{stripped}"
    if os.path.isdir(local):
        rclone.copy(os.path.abspath(local), target)
    else:
        rclone.copyto(os.path.abspath(local), f"{target}/{os.path.basename(local)}")


def download(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    remote_path: str,
    local: str,
) -> None:
    account = cfg.account(name)
    remote = sanitize_remote_name(account.name)
    source = _normalise_remote_target(remote_path).strip("/")
    src = remote if not source else f"{remote}:{source}"
    entries = rclone.list(remote, source or "/", dirs_only=True, recursive=False)
    is_dir = bool(entries)
    if is_dir or source == "Root":
        rclone.copy(src, os.path.abspath(local))
    else:
        rclone.copyto(src, os.path.abspath(local))


def sync_path(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    local: str,
    remote_path: str,
    mode: str = "push",
) -> None:
    account = cfg.account(name)
    remote = sanitize_remote_name(account.name)
    stripped = _normalise_remote_target(remote_path).strip("/")
    dest = f"{remote}:{stripped}" if stripped else f"{remote}:"
    local_abs = os.path.abspath(local)
    if mode == "push":
        rclone.sync(local_abs, dest)
    elif mode == "pull":
        rclone.sync(dest, local_abs)
    elif mode == "both":
        rclone.bisync(local_abs, dest)
    else:
        raise ValueError(f"unknown sync mode: {mode}")


def run_jobs(
    cfg: Config,
    rclone: Rclone,
    *,
    job_names: list[str] | None = None,
) -> list[dict]:
    selected = (
        [cfg.job(n) for n in job_names]
        if job_names
        else list(cfg.jobs.values())
    )
    results: list[dict] = []
    for job in selected:
        done = {"job": job.name, "mode": job.mode, "ok": True, "error": None}
        try:
            sync_path(
                cfg,
                rclone,
                name=job.account,
                local=job.local,
                remote_path=job.remote,
                mode=job.mode,
            )
        except Exception as exc:  # noqa: BLE001 - surface to CLI
            done["ok"] = False
            done["error"] = str(exc)
        results.append(done)
    return results