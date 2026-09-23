# AGENTS.md

Guidance for agents working in this repository.

## Project

`megamendung` is a rewrite of the abandoned `xiphoideuz/megamendung` fork (of
[mega_manager](https://github.com/szmania/mega_manager)). It is a CLI that
manages multiple MEGA account via rclone:

- Enlist existing accounts (`accounts import/add`), create new ones with
  plus-alias signup (`accounts create` + `accounts verify`), keep them alive
  (`refresh`), and manage files (`df`, `ls`, `mkdir`, `rm`, `upload`,
  `download`, `sync`, `sync-jobs`, `compress`).

The web GUI lives in a separate repository
([xiphoideuz/megamendung-gui](https://github.com/xiphoideuz/megamendung-gui)):
a Cloudflare Worker relay + dashboard. This CLI dials out to it with
`megamendung connect <worker-url>`; pairing codes come from
`megamendung pair`.

- `legacy/` holds the original mega_manager source (reference only).

## Layout

- `megamendung/` - the Python package (CLI).
- `tests/` - pytest suite for the CLI (offline; independent WS server stub).
- `legacy/` - original mega_manager (do not build/package).

## Commands

CLI (Python >= 3.10; system pip is PEP 668-managed on dev machines, so use a
venv):

    python3 -m pytest tests -q          # run CLI unit tests (needs websocket-client)
    python3 -m compileall megamendung tests
    pip install -e .                    # in a venv, not system pip

Web GUI (not in this repo - see xiphoideuz/megamendung-gui):

    npm install && npm test && npm run typecheck && npm run dev -- --port 8790

## Conventions

- Config is INI (`~/.config/megamendung/megamendung.conf`); passwords are
  rclone-obscured. The user's personal `~/.config/rclone/rclone.conf` must
  never be modified - megamendung uses its own `rclone.conf`.
- `MEGAMENDUNG_CONFIG_DIR` env override points tests at scratch dirs.
- All storage ops shell out to rclone; do not implement the MEGA protocol in
  the CLI. The MEGA REST/signup client lives in `megamendung/mega_api.py`.
- WebSocket protocol (JSON frames): browser/agent both present the pairing
  code (`<pair_id>:<secret>`) in the first `hello`; agent binds the room
  (trust-on-first-use), clients request RPC; agent handles
  `{id, method, params}` and replies `{type:"reply", id, ok, result|error}`;
  the agent streams `{type:"event", ...}` progress. See `megamendung/connect.py`.

## Gotchas

- Do not add comments to code unless asked.
- Keep the CLI fully functional without the Worker (the GUI is additive).
- Default `wrangler dev` port is 8787, which is owned locally by the `nummon`
  project on this machine - use `--port 8790` for GUI integration runs.