"""Portable single-file config (accounts + remotes + pending) and export mode."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from megamendung.config import Config, default_config_path
from megamendung.paths import default_rclone_config_path
from megamendung.rclone_backend import Rclone
from megamendung.accounts import export_accounts, _load_pending, _save_pending


def test_default_rclone_config_is_the_config_file():
    """megamendung.conf is now the single portable rclone config."""
    assert default_rclone_config_path() == default_config_path()


def test_config_roundtrips_pending(tmp_path: Path):
    conf = tmp_path / "megamendung.conf"
    cfg = Config(path=conf)
    cfg.settings["base_email"] = "a@b.com"
    cfg.pending["mega1"] = "pk:challenge:user_handle"
    cfg.save()

    loaded = Config.load(conf)
    assert loaded.pending["mega1"] == "pk:challenge:user_handle"
    assert "mega1" not in loaded.accounts


def test_pending_lives_in_config_not_json(tmp_path: Path):
    """After _save_pending the data is in megamendung.conf, not pending.json."""
    conf = tmp_path / "megamendung.conf"
    cfg = Config(path=conf)
    cfg.pending = {"mega1": "the-recovery"}
    _save_pending(cfg, {"mega1": "the-recovery"})
    cfg.save()
    assert cfg.pending == {"mega1": "the-recovery"}
    assert (tmp_path / "pending.json").exists() is False
    loaded = Config.load(conf)
    assert loaded.pending == {"mega1": "the-recovery"}


class _FakeRclone(Rclone):
    def reveal(self, obscured: str) -> str:
        return "plain:" + obscured[:6]


def test_export_accounts(tmp_path: Path):
    conf = tmp_path / "megamendung.conf"
    cfg = Config(path=conf)
    from megamendung.config import Account
    cfg.accounts["mega1"] = Account(
        name="mega1", email="me+mega1@gmail.com", password="OBSCURED_XYZ"
    )
    cfg.pending["mega1"] = "pk:ch:handle"
    rows = export_accounts(cfg, _FakeRclone())
    assert rows == [
        {"name": "mega1", "email": "me+mega1@gmail.com",
         "password": "plain:OBSCUR", "recovery_key": "pk:ch:handle"}
    ]


def _cli():
    return str(Path("/tmp/opencode/venv/bin/megamendung").resolve())


def test_export_cli(tmp_path: Path, monkeypatch):
    megamendung = _cli()
    env = dict(__import__("os").environ)
    env["MEGAMENDUNG_CONFIG_DIR"] = str(tmp_path)
    (tmp_path / "megamendung.conf").write_text(
        "[settings]\nbase_email = a@b.com\n"
        "[account.m1]\nemail = me+m1@gmail.com\n"
        "password = qYiu37_Xmy1-pPbtnNrizbKS1wFjiYmQRbAHUQ\n"
        "[m1]\ntype = mega\nuser = me+m1@gmail.com\npass = qYiu37_Xmy1-pPbtnNrizbKS1wFjiYmQRbAHUQ\n"
    )
    out = subprocess.run(
        [megamendung, "accounts", "export", "--force"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert "m1" in out.stdout
    assert "me+m1@gmail.com" in out.stdout
    # password is *revealed* (exported)
    assert "Kosongin aja" in out.stdout
    assert "qYiu37" not in out.stdout
    assert "RECOVERY" in out.stdout  # header present


def test_export_cli_json(tmp_path: Path, monkeypatch):
    megamendung = _cli()
    env = dict(__import__("os").environ)
    env["MEGAMENDUNG_CONFIG_DIR"] = str(tmp_path)
    (tmp_path / "megamendung.conf").write_text(
        "[settings]\nbase_email = a@b.com\n"
        "[account.m1]\nemail = me+m1@gmail.com\n"
        "password = qYiu37_Xmy1-pPbtnNrizbKS1wFjiYmQRbAHUQ\n"
        "[m1]\ntype = mega\nuser = me+m1@gmail.com\npass = qYiu37_Xmy1-pPbtnNrizbKS1wFjiYmQRbAHUQ\n"
    )
    out = subprocess.run(
        [megamendung, "accounts", "export", "--force", "--json"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    rows = json.loads(out.stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "m1"
    # recovery key empty for a plain-added (non-signup) account
    assert rows[0]["recovery_key"] == ""
    # password revealed
    assert rows[0]["password"] == "Kosongin aja"