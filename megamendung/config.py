"""rclone-config-style INI store for megamendung.

Format (``~/.config/megamendung/megamendung.conf``)::

    [settings]
    base_email = aslamyfaniadora@gmail.com
    base_name  = mega
    remote_prefix =            # optional namespace for rclone remotes megamendung creates
    last_alias_index = 3

    [account.mega1]
    email      = aslamyfaniadora+mega1@gmail.com
    password   = <rclone-obscured>
    verified   = true
    created    = 2026-09-22T12:00:00Z
    last_login = 2026-09-22T18:30:00Z
    notes      =

    [sync.camera]
    account = mega1
    local   = /data/camera
    remote  = /Root/camera
    mode    = push
"""

from __future__ import annotations

import os
from configparser import ConfigParser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .paths import default_config_path

ACCOUNT_SECTION_PREFIX = "account."
JOB_SECTION_PREFIX = "sync."

RCLONE_PREFIX_ENV = "MEGAMENDUNG_RCLONE_PREFIX"


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader for an optional ``.env`` next to the config.

    Exported shell variables win: values only land in ``os.environ`` when not
    already set (``setdefault``). Lines may use ``export KEY=...`` or bare
    ``KEY=...``; quotes are stripped; comments and blanks ignored.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Account:
    name: str
    email: str
    password: str  # stored rclone-obscured
    verified: bool = False
    created: str = ""
    last_login: str = ""
    last_status: str = ""  # "ok" | "failed" | ""
    notes: str = ""

    def to_section(self, parser: ConfigParser) -> None:
        parser[ACCOUNT_SECTION_PREFIX + self.name] = {
            "email": self.email,
            "password": self.password,
            "verified": "true" if self.verified else "false",
            "created": self.created,
            "last_login": self.last_login,
            "last_status": self.last_status,
            "notes": self.notes,
        }

    @classmethod
    def from_section(cls, name: str, section: dict[str, str]) -> "Account":
        return cls(
            name=name,
            email=section.get("email", ""),
            password=section.get("password", ""),
            verified=section.get("verified", "false").strip().lower() == "true",
            created=section.get("created", ""),
            last_login=section.get("last_login", ""),
            last_status=section.get("last_status", ""),
            notes=section.get("notes", ""),
        )


@dataclass
class SyncJob:
    name: str
    account: str
    local: str
    remote: str
    mode: str = "push"  # push | pull | both

    def to_section(self, parser: ConfigParser) -> None:
        parser[JOB_SECTION_PREFIX + self.name] = {
            "account": self.account,
            "local": self.local,
            "remote": self.remote,
            "mode": self.mode,
        }

    @classmethod
    def from_section(cls, name: str, section: dict[str, str]) -> "SyncJob":
        return cls(
            name=name,
            account=section.get("account", ""),
            local=section.get("local", ""),
            remote=section.get("remote", ""),
            mode=section.get("mode", "push"),
        )


@dataclass
class Config:
    path: Path
    settings: dict[str, str] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    jobs: dict[str, SyncJob] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)  # unverified signup state

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path or default_config_path())
        _load_dotenv(path.parent / ".env")
        cfg = cls(path=path)
        parser = ConfigParser(interpolation=None)
        parser.read(path)
        for section in parser.sections():
            if section == "settings":
                cfg.settings = dict(parser[section])
            elif section.startswith(ACCOUNT_SECTION_PREFIX):
                name = section[len(ACCOUNT_SECTION_PREFIX):]
                cfg.accounts[name] = Account.from_section(name, dict(parser[section]))
            elif section.startswith(JOB_SECTION_PREFIX):
                name = section[len(JOB_SECTION_PREFIX):]
                cfg.jobs[name] = SyncJob.from_section(name, dict(parser[section]))
            elif section == "pending":
                cfg.pending = dict(parser[section])
        return cfg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        parser = ConfigParser(interpolation=None)
        # keep any sections we don't manage (e.g. rclone remotes that live
        # in the same portable file) by reading the existing file first.
        parser.read(self.path)
        parser["settings"] = self.settings
        for account in self.accounts.values():
            account.to_section(parser)
        for job in self.jobs.values():
            job.to_section(parser)
        if self.pending:
            parser["pending"] = self.pending
        with open(self.path, "w") as fh:
            parser.write(fh)

    # -- convenience ------------------------------------------------------

    def account(self, name_or_email: str) -> Account:
        acc = self.accounts.get(name_or_email)
        if acc is None:
            for candidate in self.accounts.values():
                if candidate.email == name_or_email:
                    return candidate
        if acc is None:
            raise KeyError(f"account not found: {name_or_email}")
        return acc

    def job(self, name: str) -> SyncJob:
        try:
            return self.jobs[name]
        except KeyError:
            raise KeyError(f"sync job not found: {name}")

    def next_alias_index(self, base: str | None = None) -> int:
        """Smallest free alias counter for the given base name."""
        base = base or self.settings.get("base_name", "mega")
        used = {
            self.accounts[name].email
            for name in self.accounts
        }
        base_email = self.settings.get("base_email", "")
        local, _, domain = base_email.partition("@")
        index = int(self.settings.get("last_alias_index", "0") or "0")
        while any(c for c in used if c == f"{local}+{base}{index}@{domain}"):
            index += 1
        self.settings["last_alias_index"] = str(index)
        return index

    def alias_email(self, index: int | None = None, base: str | None = None) -> str:
        base = base or self.settings.get("base_name", "mega")
        if index is None:
            index = self.next_alias_index(base)
        base_email = self.settings.get("base_email", "")
        if not base_email:
            raise KeyError("set settings.base_email before creating accounts")
        local, _, domain = base_email.partition("@")
        return f"{local}+{base}{index}@{domain}"


def resolve_remote_prefix(cfg: Config) -> str:
    """Namespace for the rclone remotes megamendung provisions.

    Precedence: env ``MEGAMENDUNG_RCLONE_PREFIX`` (or ``<config-dir>/.env``,
    loaded by ``Config.load``) over the ``remote_prefix`` setting.
    """
    return (
        os.environ.get(RCLONE_PREFIX_ENV, "").strip()
        or cfg.settings.get("remote_prefix", "").strip()
    )