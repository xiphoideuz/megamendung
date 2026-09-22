"""Offline tests for the INI config layer, aliasing, units, and helpers."""

from __future__ import annotations

from pathlib import Path

from megamendung.config import Account, Config, SyncJob
from megamendung.manage import human_bytes
from megamendung.rclone_backend import sanitize_remote_name


def test_account_section_roundtrip():
    acc = Account(
        name="mega1",
        email="aslamyfaniadora+mega1@gmail.com",
        password="XXXX",
        verified=True,
        created="2026-09-22T12:00:00Z",
        notes="imported from rclone",
    )
    import configparser

    parser = configparser.ConfigParser(interpolation=None)
    acc.to_section(parser)
    assert "account.mega1" in parser.sections()
    assert parser["account.mega1"]["email"] == "aslamyfaniadora+mega1@gmail.com"
    restored = Account.from_section("mega1", dict(parser["account.mega1"]))
    assert restored == acc
    assert restored.verified is True


def test_config_ini_roundtrip(tmp_path: Path):
    path = tmp_path / "megamendung.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(path=path)
    cfg.settings["base_email"] = "aslamyfaniadora@gmail.com"
    cfg.settings["base_name"] = "mega"
    cfg.accounts["mega1"] = Account(
        name="mega1", email="aslamyfaniadora+mega1@gmail.com", password="XXXX", verified=True
    )
    cfg.jobs["camera"] = SyncJob(name="camera", account="mega1", local="/data/camera", remote="/Root/camera", mode="both")
    cfg.save()

    loaded = Config.load(path)
    assert loaded.settings["base_email"] == "aslamyfaniadora@gmail.com"
    assert set(loaded.accounts) == {"mega1"}
    assert loaded.accounts["mega1"].verified is True
    assert loaded.jobs["camera"].mode == "both"
    assert loaded.jobs["camera"].remote == "/Root/camera"


def test_alias_email_and_next_index(tmp_path: Path):
    path = tmp_path / "megamendung.conf"
    cfg = Config(path=path)
    cfg.settings["base_email"] = "aslamyfaniadora@gmail.com"
    cfg.settings["base_name"] = "mega"

    first = cfg.next_alias_index()  # 0
    assert cfg.alias_email(first) == "aslamyfaniadora+mega0@gmail.com"
    cfg.accounts["mega0"] = Account(name="mega0", email="aslamyfaniadora+mega0@gmail.com", password="XXXX")
    second = cfg.next_alias_index()  # skip to 1
    assert second == 1
    assert cfg.alias_email(second) == "aslamyfaniadora+mega1@gmail.com"


def test_alias_email_custom_base(tmp_path: Path):
    cfg = Config(path=tmp_path / "x")
    cfg.settings["base_email"] = "me@example.org"
    assert cfg.alias_email(3, base="archive") == "me+archive3@example.org"


def test_account_lookup_by_email_or_name(tmp_path: Path):
    path = tmp_path / "megamendung.conf"
    cfg = Config(path=path)
    cfg.accounts["mega1"] = Account(name="mega1", email="me+mega1@gmail.com", password="XXXX")
    assert cfg.account("mega1").email == "me+mega1@gmail.com"
    assert cfg.account("me+mega1@gmail.com").name == "mega1"
    import pytest

    with pytest.raises(KeyError):
        cfg.account("nope")


def test_human_bytes():
    assert human_bytes(None) == "-"
    assert human_bytes(0) == "0 B"
    assert human_bytes(1500) == "1.5 KiB"
    assert human_bytes(2 * 1024 * 1024) == "2.0 MiB"
    assert human_bytes(3 * 1024 * 1024 * 1024) == "3.0 GiB"


def test_sanitize_remote_name():
    assert sanitize_remote_name("mega1") == "mega1"
    assert sanitize_remote_name("my.account@x") == "my_account_x"
    assert sanitize_remote_name("___") == "account"