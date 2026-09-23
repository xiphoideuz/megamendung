"""'refresh login' - the anti-churn liveness pass.

MEGA marks long-idle accounts for recycling; a periodic login keeps them
active. ``rclone`` authenticates on every operation, so a cheap authenticated
probe (``rclone about``) both *refreshes the login* and *proves the stored
password still works*.

Designed for cron: prints a one-line-per-account result, updates
``last_login``/``last_status`` in the registry, and exits non-zero if any
account failed (so a mail/notification can pick it up).
"""

from __future__ import annotations

import time

from .config import Config, utcnow
from .rclone_backend import Rclone, RcloneError
from .accounts import _reveal, _provision, AccountError


class RefreshResult:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.failed: list[str] = []
        self.errors: dict[str, str] = {}

    @property
    def success(self) -> bool:
        return not self.failed


def refresh_accounts(
    cfg: Config,
    rclone: Rclone,
    *,
    names: list[str] | None = None,
    sleep: float = 2.0,
    provision: bool = True,
) -> RefreshResult:
    result = RefreshResult()
    selected = (
        [cfg.account(n) for n in names]
        if names
        else list(cfg.accounts.values())
    )
    if not selected and not names:
        # no accounts at all is a no-op, not an error
        return result

    for index, account in enumerate(selected):
        if index:
            time.sleep(sleep)  # respect MEGA's login rate limit
        remote = rclone.remote_name(account.name)
        try:
            if provision:
                _provision(rclone, account, _reveal(rclone, account.password))
            rclone.healthcheck(remote, timeout=30)
        except (RcloneError, AccountError) as exc:
            account.last_status = "failed"
            account.last_login = utcnow()
            cfg.accounts[account.name] = account
            result.failed.append(account.name)
            result.errors[account.name] = str(exc)
        else:
            account.last_status = "ok"
            account.last_login = utcnow()
            cfg.accounts[account.name] = account
            result.ok.append(account.name)
    cfg.save()
    return result