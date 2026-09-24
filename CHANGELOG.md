# Changelog

## 2.0.1 (2026-09-23)
### Added
- Single portable config file: ``megamendung.conf`` now holds the account
  registry, the rclone ``mega`` remotes, and any pending signup state in
  one transferable file (the old separate ``rclone.conf``/``pending.json``
  are gone).
- ``accounts export`` mode: dump name, email, plaintext password and
  recovery key as a table (``--force``) or JSON/``--file``.
- Rclone remote prefix: namespace the remotes megamendung creates via
  `remote_prefix` in `[settings]`, a ``MEGAMENDUNG_RCLONE_PREFIX`` env var,
  or a `<config-dir>/.env` file (precedence in that order).

### Changed
- Split the web GUI out of this repo into the separate
  `xiphoideuz/megamendung-gui` repository (Cloudflare Worker + SPA). The CLI
  keeps `pair`/`connect`; see `megamendung-gui` for deploying the dashboard.

## Version 2.0.0 (2026-09-22)
### Added
- Complete rewrite as a modern CLI (`megamendung`), rclone-config style.
- Account lifecycle: `list`, `add`, `import` (from an rclone config or
  credentials file), `show`, `remove`.
- Automatic account signup via the MEGA registration API using plus-alias
  emails (`accounts create`), with `accounts verify` to finish signups.
- Cron-friendly `refresh` command that performs a real login against each
  account and exits non-zero on any failure.
- Storage tools ported from mega_manager on top of rclone: `df`, `ls`,
  `mkdir`, `rm`, `upload`, `download`, `sync` (push/pull/both).
- Declarative sync jobs in the config (`[sync.<name>]`) run via `sync-jobs`.
- Image (Pillow) and video (ffmpeg) compression with a state file so files
  are only processed once.
- `pair` and `connect` subcommands: WhatsApp-Web-style device pairing for the
  web GUI (CLI dials out to a Cloudflare Worker relay, no inbound ports).
- `web/` Cloudflare Worker project: a Tailwind SPA dashboard (accounts,
  files, sync jobs, signup, compression) in front of a per-pairing RelayDO.
- `MEGAMENDUNG_CONFIG_DIR` env override for the config directory.

### Changed
- Backend switched from the hand-rolled megatools-style storage protocol to
  rclone's maintained `mega` backend.
- Account registry stored in its own `~/.config/megamendung/megamendung.conf`
  and rclone remotes in `~/.config/megamendung/rclone.conf`; the user's
  personal rclone config is never modified.
- Packaging moved from `setup.py` to `pyproject.toml` (PEP 621), dropping the
  unmaintained `setup.py`/`requirements.txt`.

### Removed
- Legacy `megamanager` package moved to `legacy/megamanager/` (the original
  mega_manager code) and is no longer packaged.

## Version 0.3.0 (2024-11-27)
### Bugs
- Fixed various bugs.

## Version 0.2.0 (2023-10-25)
### Bugs
- Fixed logging handlers, removed hardcoded timeout for waiting for threads.

## Version 0.0.3 (2023-01-01)
- Initial upstream mega_manager releases.