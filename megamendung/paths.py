"""Filesystem paths / config directory resolution for megamendung."""

from __future__ import annotations

import os
from pathlib import Path

ENV_CONFIG_DIR = "MEGAMENDUNG_CONFIG_DIR"


def default_config_dir() -> Path:
    env = os.environ.get(ENV_CONFIG_DIR)
    if env:
        return Path(env).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "megamendung"


def default_config_path() -> Path:
    return default_config_dir() / "megamendung.conf"


def default_rclone_config_path() -> Path:
    # The rclone config is the same portable megamendung.conf, so one
    # file transfers everything (accounts + mega remotes).
    return default_config_path()


def default_state_path() -> Path:
    return default_config_dir() / "state.json"


def ensure_config_dir() -> Path:
    d = default_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d