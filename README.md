# megamendung

One-stop **MEGA account manager**, rclone-config style.

`megamendung` is the modernized successor of the abandoned
[mega_manager](https://github.com/szmania/mega_manager) fork
(`xiphoideuz/megamendung`). It manages *multiple* [mega.nz](https://mega.nz)
accounts — enlisting existing ones, signing up new ones via plus-alias
emails, keeping them alive, and running the classic mega_manager storage
tools (sync, upload, download, df, ls, mkdir, rm, compress) — all driven by
[rclone](https://rclone.org)'s first-class `mega` backend.

> **Why rclone?** rclone has a robust, maintained MEGA backend (encryption,
> chunked uploads, resume) and already handles the hard storage protocol.
> mega_manager's Python port of that protocol was unmaintained and broken.

## Requirements

- Python >= 3.10
- [rclone](https://rclone.org/downloads/) >= 1.60 on `PATH`
- `ffmpeg` (only for `compress --videos`), Pillow (bundled) for `compress`
- Network access to `g.api.mega.co.nz` for account registration

Install with `pip install .` (or `pip install -e .` for development).

## Setup

Every command except `config` needs at least one configured account.

### 1. Set the base email (for creating alias accounts)

Plus-alias signups derive email addresses like `me+mega1@gmail.com` from a
base address you own:

    megamendung config you@example.com --base-name mega

All settings live in `~/.config/megamendung/megamendung.conf`.

### 2a. Enlist an account you already have

Import it straight from your existing rclone config
(`~/.config/rclone/rclone.conf`, the one with `[mega1] type = mega`):

    megamendung accounts import --rclone-conf ~/.config/rclone/rclone.conf

or register credentials by hand:

    megamendung accounts add NAME EMAIL PASSWORD [--verified]

or from a credentials file, one `<email> <password>` per line (`#` for
comments):

    megamendung accounts import --credentials-file ./creds.txt

### 2b. Create brand-new accounts (auto-signup)

`megamendung` implements MEGA's public registration API (the same flow as
`megareg`) and creates non-verified accounts under your base email using
plus-aliases. A random password is generated per account.

    megamendung accounts create --count 3

MEGA emails a "MEGA Signup" confirmation link to each new address. Paste it
to finish each signup (the registration state is saved automatically):

    megamendung accounts verify mega2 'https://mega.nz/#confirm<signup_key>'

## Managing accounts

    megamendung accounts list                # configured + detected accounts
    megamendung accounts list --json         # machine-readable
    megamendung accounts show mega1          # details for one account
    megamendung accounts remove mega1        # drop registry entry + rclone remote

### Keep accounts alive (cron-friendly)

MEGA can deactivate accounts you don't log into. `refresh` performs a real
login against each account (via rclone's `mega` backend) and exits non-zero
if any account fails:

    megamendung refresh                       # all accounts, 2s between logins
    megamendung refresh mega1 mega2           # only named accounts

Example cron line (daily at 06:00):

    0 6 * * * /usr/local/bin/megamendung refresh >> /var/log/megamendung-refresh.log 2>&1

## Files

    megamendung ls   mega1 /Root/photos
    megamendung ls -R mega1 /Root

## Storage usage

    megamendung df                            # used / free / total per account

## Upload / download

    megamendung upload   mega1 ./photos /Root/photos
    megamendung download mega1 /Root/archive /data/archive

## Sync

`push` mirrors local → remote, `pull` mirrors remote → local,
`both` does two-way synchronized sync:

    megamendung sync mega1 /data/camera /Root/camera --mode push
    megamendung sync mega1 /data/camera /Root/camera --mode both

### Cron sync jobs

Define jobs in `megamendung.conf`:

    [sync.camera]
    account = mega1
    local   = /data/camera
    remote  = /Root/camera
    mode    = push

then run them all (or a subset) with:

    megamendung sync-jobs
    megamendung sync-jobs camera

## Compress

Optimize JPEGs (Pillow) and videos (ffmpeg/x264) in place, tracking progress
in `state.json` so files are only processed once:

    megamendung compress /data/media
    megamendung compress /data/media --videos --crf 28
    megamendung compress /data/media --images --max-dimension 2048 --quality 80
    megamendung compress /data/media --force     # reprocess everything

## Web GUI (Cloudflare Worker)

`web/` ships a WhatsApp-Web-style GUI: a **Cloudflare Worker** (perpetual,
free) serves a browser dashboard and relays commands to your machine over
WebSocket. rclone/ffmpeg still run on your machine - the Worker never does
storage work, so all features (including video compression) keep working.

```
Browser (Cloudflare)  <──wss──>  Worker RelayDO (pairing per pair_id)  <──wss──>  megamendung connect
```

Flow:

1. Deploy the Worker:

       cd web
       npm install
       npm run dev        # local preview (default port may conflict; use --port 8790)
       npm run deploy     # wrangler deploy

   (create the KV namespace first with `wrangler kv namespace create PAIRS`
   and paste its id into `web/wrangler.toml`, or delete the binding).

2. On the machine that runs rclone, print a pairing code:

       megamendung pair

3. Connect the machine to the Worker (dials *out* - no open ports):

       megamendung connect https://<your-worker>.workers.dev

4. Open `https://<your-worker>.workers.dev` in the browser, paste the same
   pairing code, and drive the Dashboard (accounts + one-click refresh),
   Files, Sync, Accounts (create/verify) and Compress panels.

The pairing code is the credential for the pair room: the first `connect`
binds the room (trust-on-first-use) and only browsers presenting the same
code can control it. Forget the code on the device to re-pair.

The CLI stays fully functional standalone; the GUI is purely additive. The
relay protocol is documented in `megamendung/connect.py` and `web/src/relay.ts`.

## Configuration

`~/.config/megamendung/megamendung.conf` — rclone-style INI:

    [settings]
    base_email     = you@example.com
    base_name      = mega
    last_alias_index = 2

    [account.mega1]
    email          = you+mega1@example.com
    password       = <rclone-obscured>
    verified       = true
    created        = 2026-09-22T12:00:00Z
    last_login     = 2026-09-22T18:30:00Z
    last_status    = ok
    notes          =

    [sync.camera]
    account        = mega1
    local          = /data/camera
    remote         = /Root/camera
    mode           = push

Passwords are stored **rclone-obscured** (as `pass` in an rclone remote
config would be). The rclone remotes megamendung manages live in its own
config, `~/.config/megamendung/rclone.conf` — your personal
`~/.config/rclone/rclone.conf` is left untouched.

Override the config directory for any command with the
`MEGAMENDUNG_CONFIG_DIR` environment variable, or point at explicit files
with `--config` and `--rclone-config` global flags.

## Ported from mega_manager

- sync (`push`/`pull`/`both`), upload, download, df, ls, mkdir, rm
- image compression (Pillow) and video compression (ffmpeg)
- multiple-account registry

The original codebase is preserved under `legacy/megamanager/` for reference.

## License

GNU General Public License v3 or later — see `LICENSE`.