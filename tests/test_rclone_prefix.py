"""Offline tests for the rclone remote prefix (settings / .env / env var)."""

from __future__ import annotations

import os
from pathlib import Path

from megamendung.accounts import list_accounts
from megamendung.config import RCLONE_PREFIX_ENV, Config, resolve_remote_prefix
from megamendung.config import Account
from megamendung.rclone_backend import Rclone, sanitize_remote_name


def test_sanitize_remote_name():
    assert sanitize_remote_name("mega1") == "mega1"
    assert sanitize_remote_name("my.account@x") == "my_account_x"
    assert sanitize_remote_name("___") == "account"


def test_remote_name_without_prefix():
    assert Rclone().remote_name("mega1") == "mega1"


def test_remote_name_with_prefix():
    assert Rclone(prefix="mg_").remote_name("mega1") == "mg_mega1"


def test_remote_name_prefix_is_sanitized():
    assert Rclone(prefix="my-prefix/").remote_name("mega1") == "my_prefix_mega1"


def test_resolve_remote_prefix_defaults_to_empty(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)
    cfg = Config(path=tmp_path / "megamendung.conf")
    assert resolve_remote_prefix(cfg) == ""


def test_resolve_remote_prefix_from_settings(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)
    cfg = Config(path=tmp_path / "megamendung.conf")
    cfg.settings["remote_prefix"] = "mg2_"
    assert resolve_remote_prefix(cfg) == "mg2_"


def test_resolve_remote_prefix_env_wins_over_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(RCLONE_PREFIX_ENV, "env_")
    cfg = Config(path=tmp_path / "megamendung.conf")
    cfg.settings["remote_prefix"] = "cfg_"
    assert resolve_remote_prefix(cfg) == "env_"


def test_load_dotenv_feeds_prefix(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)
    conf = tmp_path / "megamendung.conf"
    conf.write_text("")
    (tmp_path / ".env").write_text(
        "# comment line\n"
        "MEGAMENDUNG_RCLONE_PREFIX=dotenv_\n"
        "export SOME_OTHER=1\n"
        'QUOTED="yes"\n'
    )
    cfg = Config.load(conf)
    assert resolve_remote_prefix(cfg) == "dotenv_"
    assert os.environ.get("SOME_OTHER") == "1"
    assert os.environ.get("QUOTED") == "yes"
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)


def test_shell_env_beats_dotenv(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(RCLONE_PREFIX_ENV, "shell_")
    conf = tmp_path / "megamendung.conf"
    conf.write_text("")
    (tmp_path / ".env").write_text("MEGAMENDUNG_RCLONE_PREFIX=dotenv_\n")
    cfg = Config.load(conf)
    assert resolve_remote_prefix(cfg) == "shell_"


def _stub_rclone_conf(path: Path, remotes: dict[str, str]) -> Path:
    """Write a megamendung.conf containing [name] mega remotes (the
    portable single-file config) so scan_rclone_conf finds them."""
    parts = []
    for name, email in remotes.items():
        parts += [f"[{name}]", "type = mega", f"user = {email}", "pass = XXXX", ""]
    path.write_text("\n".join(parts))
    return path


def test_list_accounts_matches_prefixed_remotes(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)
    conf = tmp_path / "megamendung.conf"
    cfg = Config(path=conf)
    cfg.accounts["mega1"] = Account(
        name="mega1", email="me+mega1@gmail.com", password="XXXX", verified=True
    )
    rclone_conf = _stub_rclone_conf(
        conf,
        {"mg_mega1": "me+mega1@gmail.com", "unrelated": "other@gmail.com"},
    )
    rclone = Rclone(config_path=rclone_conf, prefix="mg_")
    rows = list_accounts(cfg, rclone)

    names = [r["name"] for r in rows]
    assert "mega1" in names  # managed account
    assert "mg_mega1" not in names  # prefixed remote matched, not re-listed
    assert "unrelated" in names  # not megamendung-managed -> shown as detected
    detected = next(r for r in rows if r["name"] == "unrelated")
    assert detected["managed"] is False


def test_list_accounts_without_prefix_matches_plain(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(RCLONE_PREFIX_ENV, raising=False)
    conf = tmp_path / "megamendung.conf"
    cfg = Config(path=conf)
    cfg.accounts["mega1"] = Account(
        name="mega1", email="me+mega1@gmail.com", password="XXXX", verified=True
    )
    rclone_conf = _stub_rclone_conf(conf, {"mega1": "me+mega1@gmail.com"})
    rows = list_accounts(cfg, Rclone(config_path=rclone_conf))
    names = [r["name"] for r in rows]
    assert names == ["mega1"]