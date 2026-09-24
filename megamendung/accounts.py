"""Account lifecycle: list, add, import, create, verify, remove.

Credentials are always stored rclone-obscured in the account registry,
which lives in the single portable ``megamendung.conf`` file (alongside
the rclone ``mega`` remotes and any pending signup state)."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from pathlib import Path

from . import mega_api
from .config import Account, Config, utcnow
from .rclone_backend import Rclone, RcloneError, sanitize_remote_name

_PW_CHARS = string.ascii_letters + string.digits + "!@#$%^&*_-"


def random_password(length: int = 20) -> str:
    return "".join(secrets.choice(_PW_CHARS) for _ in range(length))


class AccountError(Exception):
    """Raised for account lifecycle problems."""


def _load_pending(cfg: Config) -> dict[str, str]:
    return dict(cfg.pending)


def _save_pending(cfg: Config, pending: dict[str, str]) -> None:
    cfg.pending = pending


@dataclass
class DetectedRemote:
    name: str
    type: str
    email: str
    password: str  # already obscured if coming from an rclone conf


def scan_rclone_conf(path: Path) -> list[DetectedRemote]:
    """Find ``mega`` remotes inside an rclone config file (main or ours)."""
    from configparser import ConfigParser

    out: list[DetectedRemote] = []
    parser = ConfigParser(interpolation=None)
    if not path.exists():
        return out
    try:
        parser.read(path)
    except Exception as exc:  # pragma: no cover
        raise AccountError(f"unable to parse rclone config {path}: {exc}")
    for section in parser.sections():
        data = dict(parser[section])
        if data.get("type", "").strip().lower() != "mega":
            continue
        email = data.get("user", "")
        password = data.get("pass", "")
        if not email:
            continue
        out.append(DetectedRemote(section, "mega", email, password))
    return out


def _provision(rclone: Rclone, account: Account, plaintext_password: str) -> None:
    """Create or refresh the rclone mega remote for an account."""
    name = rclone.remote_name(account.name)
    try:
        existing = rclone.listremotes()
        if name in existing:
            rclone.config_update(name, user=account.email, pass_=plaintext_password)
        else:
            rclone.config_create(name, user=account.email, pass_=plaintext_password)
    except RcloneError as exc:
        raise AccountError(f"failed to provision rclone remote for {account.name}: {exc}")


def _reveal(rclone: Rclone, obscured: str) -> str:
    """Return the plaintext password for an obscured value (rclone or raw)."""
    return rclone.reveal(obscured)


def store_password(rclone: Rclone, candidate: str) -> str:
    """Normalise a (possibly already obscured) password to rclone-obscured form."""
    if candidate.startswith("~"):
        raise AccountError("password looks like an env reference; not supported")
    try:
        plain = rclone.reveal(candidate)
        if plain:
            # already obscured - keep exactly as stored
            return candidate
    except RcloneError:
        pass
    return rclone.obscure(candidate)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def list_accounts(cfg: Config, rclone: Rclone) -> list[dict]:
    rows: list[dict] = []
    for account in cfg.accounts.values():
        row = {
            "name": account.name,
            "email": account.email,
            "verified": account.verified,
            "created": account.created,
            "last_login": account.last_login,
            "last_status": account.last_status,
            "managed": True,
        }
        rows.append(row)
    for detected in scan_rclone_conf(rclone.config_path):
        managed = {rclone.remote_name(a.name) for a in cfg.accounts.values()}
        if detected.name in managed:
            continue
        rows.append(
            {
                "name": detected.name,
                "email": detected.email,
                "verified": None,
                "created": "",
                "last_login": "",
                "last_status": "",
                "managed": False,
                "source": f"rclone:{rclone.config_path}",
            }
        )
    return rows


def add_account(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    email: str,
    password: str,
    verified: bool = False,
    notes: str = "",
) -> Account:
    name = sanitize_remote_name(name)
    if name in cfg.accounts:
        raise AccountError(f"account already exists: {name}")
    if any(a.email == email for a in cfg.accounts.values()):
        raise AccountError(f"an account with email {email} already exists")
    obscured = rclone.obscure(password)
    account = Account(
        name=name,
        email=email,
        password=obscured,
        verified=verified,
        created=utcnow(),
        notes=notes,
    )
    _provision(rclone, account, password)
    cfg.accounts[name] = account
    cfg.save()
    return account


def import_accounts(
    cfg: Config,
    rclone: Rclone,
    *,
    rclone_conf: Path | None = None,
    credentials_file: Path | None = None,
) -> list[Account]:
    imported: list[Account] = []
    if rclone_conf is not None:
        for remote in scan_rclone_conf(rclone_conf):
            name = sanitize_remote_name(remote.name)
            if rclone.prefix and name.startswith(rclone.prefix):
                name = name[len(rclone.prefix):]
            if name in cfg.accounts:
                continue
            obscured = store_password(rclone, remote.password) if remote.password else ""
            if not obscured:
                raise AccountError(f"remote {remote.name} has no password")
            account = Account(
                name=name,
                email=remote.email,
                password=obscured,
                verified=True,
                created=utcnow(),
                notes="imported from rclone",
            )
            try:
                plain = _reveal(rclone, obscured)
                _provision(rclone, account, plain)
            except RcloneError:
                # keep registry entry even if provisioning fails (report to caller)
                account.notes += " (rclone remote provisioning failed)"
            if name in cfg.accounts:
                continue
            cfg.accounts[name] = account
            imported.append(account)
    if credentials_file is not None:
        lines = [l for l in (credentials_file.read_text().splitlines() if credentials_file.exists() else []) if l.strip()]
        for line in lines:
            if line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split() if p.strip()]
            if len(parts) < 2:
                continue
            email, password = parts[0], parts[1]
            if any(a.email == email for a in cfg.accounts.values()):
                continue
            idx = 0
            name = "account"
            while f"{name}{idx}" in cfg.accounts:
                idx += 1
            add_account(cfg, rclone, name=f"{name}{idx}", email=email, password=password)
            imported.append(cfg.accounts[f"{name}{idx}"])
    cfg.save()
    return imported


def create_accounts(
    cfg: Config,
    rclone: Rclone,
    *,
    count: int,
    base_name: str | None = None,
    email: str | None = None,
    password: str | None = None,
    display_name: str | None = None,
) -> list[Account]:
    if not cfg.settings.get("base_email"):
        raise AccountError(
            "no base email configured. Set it with `megamendung config EMAIL` "
            "or edit settings.base_email in the config file."
        )
    if email and count > 1:
        raise AccountError("--email overrides the generated alias and can only be used with a single account")
    api = mega_api.MegaApi()
    pending = _load_pending(cfg)
    created: list[Account] = []
    for _ in range(count):
        base = base_name or cfg.settings.get("base_name", "mega")
        index = cfg.next_alias_index(base)
        alias = email if email else cfg.alias_email(index, base)
        name = f"{base}{index}"
        pw = password or random_password()
        full_name = display_name or f"{base.capitalize()} {index}"
        try:
            state = api.register(alias, pw, full_name)
        except mega_api.MegaApiError as exc:
            raise AccountError(f"signup failed for {alias}: {exc}")
        obscured = rclone.obscure(pw)
        account = Account(
            name=name,
            email=alias,
            password=obscured,
            verified=False,
            created=utcnow(),
            notes="pending email confirmation",
        )
        _provision(rclone, account, pw)
        cfg.accounts[name] = account
        pending[name] = state.serialize()
        cfg.settings["last_alias_index"] = str(index)
        created.append(account)
    cfg.save()
    _save_pending(cfg, pending)
    return created


def verify_account(
    cfg: Config,
    rclone: Rclone,
    *,
    name: str,
    confirm_link: str,
) -> Account:
    account = cfg.account(name)
    pending = _load_pending(cfg)
    state_serialized = pending.get(name)
    if not state_serialized:
        raise AccountError(
            f"no pending verification for {name}; run `megamendung accounts create` first"
        )
    state = mega_api.RegState.deserialize(state_serialized, account.email)
    api = mega_api.MegaApi()
    try:
        api.verify(state, confirm_link)
    except mega_api.MegaApiError as exc:
        raise AccountError(f"verification failed for {name}: {exc}")
    account.verified = True
    account.last_login = utcnow()
    account.last_status = "ok"
    account.notes = account.notes.replace("pending email confirmation", "verified")
    cfg.accounts[name] = account
    pending.pop(name, None)
    cfg.save()
    _save_pending(cfg, pending)
    return account


def remove_account(cfg: Config, rclone: Rclone, *, name: str) -> None:
    account = cfg.account(name)
    pending = _load_pending(cfg)
    rclone.config_delete(rclone.remote_name(account.name))
    cfg.accounts.pop(account.name, None)
    pending.pop(account.name, None)
    cfg.save()
    _save_pending(cfg, pending)


def show_account(cfg: Config, rclone: Rclone, *, name: str) -> dict:
    account = cfg.account(name)
    plain = "****"
    try:
        plain = _reveal(rclone, account.password)
    except RcloneError:
        pass
    remote = rclone.remote_name(account.name)
    return {
        "name": account.name,
        "email": account.email,
        "verified": account.verified,
        "password_hint": plain[:3] + "…" if len(plain) > 3 else "****",
        "created": account.created,
        "last_login": account.last_login,
        "last_status": account.last_status,
        "notes": account.notes,
        "rclone_remote": remote,
    }


def export_accounts(cfg: Config, rclone: Rclone) -> list[dict]:
    """Dump accounts as name | email | plaintext password | recovery key."""
    pending = _load_pending(cfg)
    rows: list[dict] = []
    for account in cfg.accounts.values():
        try:
            plain = _reveal(rclone, account.password)
        except RcloneError:
            plain = ""
        rows.append(
            {
                "name": account.name,
                "email": account.email,
                "password": plain,
                "recovery_key": pending.get(account.name, ""),
            }
        )
    return rows