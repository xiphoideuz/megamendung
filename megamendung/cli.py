"""megamendung command line interface.

One-stop MEGA account manager (rclone-config style).

    megamendung accounts list
    megamendung accounts create --count 3
    megamendung accounts verify mega2 '<confirm URL from email>'
    megamendung refresh                 # cron-friendly anti-churn login refresh
    megamendung df | ls | mkdir | rm | upload | download | sync
    megamendung pair                    # pairing code for the web GUI
    megamendung connect <worker-url>    # dial out and serve the browser GUI
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .accounts import (
    AccountError,
    add_account,
    create_accounts,
    import_accounts,
    list_accounts,
    remove_account,
    show_account,
    verify_account,
)
from .config import Config
from .connect import ConnectError, HEARTBEAT_SECONDS, run_connect
from .manage import (
    df,
    download,
    human_bytes,
    ls,
    mkdir,
    rm,
    run_jobs,
    sync_path,
    upload,
)
from .paths import default_config_path, default_rclone_config_path
from .refresh import refresh_accounts
from .rclone_backend import Rclone, RcloneError

ERRORS = (AccountError, RcloneError, KeyError, ValueError, OSError)


class CliError(Exception):
    """User-facing CLI error."""


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def _emit(text: str = "") -> None:
    print(text)


def _err(message: str) -> None:
    print(f"megamendung: error: {message}", file=sys.stderr)


def _print_accounts(rows: list[dict]) -> None:
    _emit(f"{'NAME':<12} {'EMAIL':<42} {'VERIFIED':<9} {'LAST LOGIN':<22} STATUS")
    _emit("-" * 100)
    for row in rows:
        verified = "yes" if row["verified"] else ("no" if row["verified"] is False else "-")
        source = row.get("source", "") if not row["managed"] else ""
        _emit(
            f"{row['name']:<12} {row['email']:<42} {verified:<9} "
            f"{row['last_login']:<22} {row['last_status'] or '-'}{('  [' + source + ']') if source else ''}"
        )


def _print_df(rows: list[dict]) -> None:
    _emit(f"{'NAME':<12} {'USED':>10} {'FREE':>10} {'TOTAL':>10} {'EMAIL':<42}")
    _emit("-" * 96)
    for row in rows:
        _emit(
            f"{row['name']:<12} {human_bytes(row['used']):>10} {human_bytes(row['free']):>10} "
            f"{human_bytes(row['total']):>10} {row['email']:<42}"
        )


def _print_ls(entries: list[dict], remote_path: str) -> None:
    prefix = remote_path.rstrip("/") + "/" if remote_path and remote_path != "/" else "/"
    for entry in entries:
        mtime = (entry.get("ModTime") or entry.get("modTime") or "")[:16]
        if entry.get("IsDir"):
            _emit(f"{entry.get('Name',''):<40} dir                                {mtime}")
        else:
            size = human_bytes(entry.get("Size"))
            _emit(f"{entry.get('Name',''):<40} {size:>12}                         {mtime}")


# ---------------------------------------------------------------------------
# command implementations
# ---------------------------------------------------------------------------


def cmd_config(args, cfg: Config, rclone: Rclone) -> int:
    if args.config_show:
        for key, value in sorted(cfg.settings.items()):
            _emit(f"{key} = {value}")
        _emit(f"config_file = {cfg.path}")
        _emit(f"rclone_config = {rclone.config_path}")
        return 0
    if args.base_email:
        cfg.settings["base_email"] = args.base_email
    if args.base_name:
        cfg.settings["base_name"] = args.base_name
    cfg.save()
    _emit(f"settings written to {cfg.path}")
    return 0


def cmd_accounts_list(args, cfg: Config, rclone: Rclone) -> int:
    rows = list_accounts(cfg, rclone)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        _print_accounts(rows)
    return 0


def cmd_accounts_add(args, cfg: Config, rclone: Rclone) -> int:
    account = add_account(
        cfg,
        rclone,
        name=args.name,
        email=args.email,
        password=args.password,
        verified=args.verified,
        notes=args.notes or "",
    )
    _print_accounts(list_accounts(cfg, rclone))
    _emit(f"\nadded {account.name} ({account.email})")
    return 0


def cmd_accounts_import(args, cfg: Config, rclone: Rclone) -> int:
    rclone_conf = Path(args.rclone_conf).expanduser() if args.rclone_conf else None
    creds = Path(args.credentials_file).expanduser() if args.credentials_file else None
    imported = import_accounts(
        cfg, rclone, rclone_conf=rclone_conf, credentials_file=creds
    )
    _print_accounts(list_accounts(cfg, rclone))
    _emit(f"\nimported {len(imported)} account(s)")
    return 0


def cmd_accounts_create(args, cfg: Config, rclone: Rclone) -> int:
    created = create_accounts(
        cfg,
        rclone,
        count=args.count,
        base_name=args.base,
        email=args.email,
        password=args.password,
        display_name=args.display_name,
    )
    _emit(f"created {len(created)} account(s). Confirm each from your inbox:")
    for account in created:
        _emit(f"  {account.email}")
    _emit(
        "\nMEGA sends a 'MEGA Signup' confirmation email per account. Finish with:\n"
        "  megamendung accounts verify NAME '<the #confirm... link from the email>'\n"
        "The registration state was saved automatically."
    )
    return 0


def cmd_accounts_verify(args, cfg: Config, rclone: Rclone) -> int:
    account = verify_account(cfg, rclone, name=args.name, confirm_link=args.link)
    _emit(f"verified {account.name} ({account.email})")
    return 0


def cmd_accounts_show(args, cfg: Config, rclone: Rclone) -> int:
    row = show_account(cfg, rclone, name=args.name)
    if args.json:
        print(json.dumps(row, indent=2, default=str))
        return 0
    for key, value in row.items():
        _emit(f"{key}: {value}")
    return 0


def cmd_accounts_remove(args, cfg: Config, rclone: Rclone) -> int:
    remove_account(cfg, rclone, name=args.name)
    _emit(f"removed {args.name}")
    return 0


def cmd_refresh(args, cfg: Config, rclone: Rclone) -> int:
    result = refresh_accounts(
        cfg, rclone, names=args.names or None, sleep=args.sleep, provision=args.no_provision is False
    )
    for name in result.ok:
        _emit(f"ok      {name}")
    for name, error in result.errors.items():
        _err(f"{name}: {error}")
    if not result.ok and not result.failed:
        _emit("no accounts configured")
    if not result.success:
        _err(f"{len(result.failed)} account(s) failed to refresh login")
        return 1
    return 0


def cmd_df(args, cfg: Config, rclone: Rclone) -> int:
    rows = df(cfg, rclone, args.names or [])
    _print_df(rows)
    return 0


def cmd_ls(args, cfg: Config, rclone: Rclone) -> int:
    entries = ls(
        cfg,
        rclone,
        name=args.name,
        remote_path=args.path or "/",
        recursive=args.recursive,
        files_only=args.files,
        dirs_only=args.dirs,
    )
    _print_ls(entries, args.path or "/")
    return 0


def cmd_mkdir(args, cfg: Config, rclone: Rclone) -> int:
    mkdir(cfg, rclone, name=args.name, remote_path=args.path)
    _emit(f"created {args.path} on {args.name}")
    return 0


def cmd_rm(args, cfg: Config, rclone: Rclone) -> int:
    rm(cfg, rclone, name=args.name, remote_path=args.path, recursive=args.recursive)
    _emit(f"removed {args.path} from {args.name}")
    return 0


def cmd_upload(args, cfg: Config, rclone: Rclone) -> int:
    upload(cfg, rclone, name=args.name, local=args.local, remote_path=args.path)
    _emit(f"uploaded {args.local} -> {args.name}:{args.path}")
    return 0


def cmd_download(args, cfg: Config, rclone: Rclone) -> int:
    download(cfg, rclone, name=args.name, remote_path=args.path, local=args.local)
    _emit(f"downloaded {args.name}:{args.path} -> {args.local}")
    return 0


def cmd_sync(args, cfg: Config, rclone: Rclone) -> int:
    sync_path(
        cfg,
        rclone,
        name=args.name,
        local=args.local,
        remote_path=args.path,
        mode=args.mode,
    )
    _emit(f"sync ({args.mode}) complete: {args.local} <-> {args.name}:{args.path}")
    return 0


def cmd_connect(args, cfg: Config, rclone: Rclone) -> int:
    code = args.code or input("pairing code (print it with `megamendung pair`): ").strip()
    return run_connect(
        args.url,
        code,
        reconnect=not args.no_reconnect,
        heartbeat=args.heartbeat,
    )


def cmd_pair(args, cfg: Config, rclone: Rclone) -> int:
    from .pair import make_pair_code

    pair = make_pair_code()
    if args.json:
        print(json.dumps({"pair_id": pair.pair_id, "secret": pair.secret, "code": pair.code}))
        return 0
    _emit("Pairing code:")
    _emit(f"  {pair.code}")
    _emit("")
    _emit("Open the megamendung web app and enter this code, then connect this machine:")
    _emit("  megamendung connect https://<your-worker>.workers.dev")
    return 0


def cmd_sync_jobs(args, cfg: Config, rclone: Rclone) -> int:
    results = run_jobs(cfg, rclone, job_names=args.jobs or None)
    failed = 0
    for row in results:
        if row["ok"]:
            _emit(f"ok     {row['job']} ({row['mode']})")
        else:
            _err(f"{row['job']}: {row['error']}")
            failed += 1
    if not results:
        _emit("no sync jobs configured")
    return 1 if failed else 0


def cmd_compress(args, cfg: Config, rclone: Rclone) -> int:
    from .compress import CompressionError, StateStore, compress_images, compress_videos
    from .paths import default_state_path

    if not args.images and not args.videos:
        args.images = True
        args.videos = True
    directory = Path(args.path).expanduser()
    if not directory.is_dir():
        raise CliError(f"not a directory: {directory}")
    state = StateStore(default_state_path())
    total_saved = 0
    try:
        if args.images:
            r = compress_images(
                directory,
                state=state,
                quality=args.quality,
                max_dimension=args.max_dimension,
                force=args.force,
            )
            total_saved += r.saved_bytes
            _emit(
                f"images: {r.images_done} optimized, {r.skipped} already done, "
                f"{r.images_failed} failed, saved {human_bytes(r.saved_bytes)}"
            )
        if args.videos:
            r = compress_videos(
                directory,
                state=state,
                preset=args.preset,
                crf=args.crf,
                force=args.force,
            )
            total_saved += r.saved_bytes
            _emit(
                f"videos: {r.videos_done} optimized, {r.skipped} already done, "
                f"{r.videos_failed} failed, saved {human_bytes(r.saved_bytes)}"
            )
    except CompressionError as exc:
        raise CliError(str(exc))
    _emit(f"total saved: {human_bytes(total_saved)}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="megamendung",
        description="One-stop MEGA account manager (rclone-config style). "
        "Enlist, create, manage and keep-alive MEGA accounts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  megamendung config aslamyfaniadora@gmail.com --base-name mega\n"
            "  megamendung accounts list\n"
            "  megamendung accounts import --rclone-conf ~/.config/rclone/rclone.conf\n"
            "  megamendung accounts create --count 3\n"
            "  megamendung accounts verify mega2 'https://mega.nz/#confirm...'\n"
            "  megamendung refresh\n"
            "  megamendung df\n"
            "  megamendung upload mega1 ./photos /Root/photos\n"
            "  megamendung download mega1 /Root/archive /data/archive\n"
            "  megamendung sync mega1 /data/camera /Root/camera --mode push\n"
            "  megamendung compress /data/media\n"
            "  megamendung pair\n"
            "  megamendung connect https://megamendung-web.<your-subdomain>.workers.dev\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"megamendung {__version__}")
    parser.add_argument("--config", type=str, default=None, help="path to megamendung.conf")
    parser.add_argument(
        "--rclone-config", type=str, default=None,
        help="path to the rclone config megamendung manages",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")

    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="show/set registry settings (base email for signups)")
    p_config.add_argument("base_email", nargs="?", default=None, help="base email for plus-alias signups (e.g. me+mega1 at gmail)")
    p_config.add_argument("--base-name", dest="base_name", default=None, help="alias marker + account name prefix (default: mega)")
    p_config.add_argument("--show", dest="config_show", action="store_true", help="print current settings")

    p_acc = sub.add_parser("accounts", help="account lifecycle")
    acc_sub = p_acc.add_subparsers(dest="subcommand", required=True)

    a_list = acc_sub.add_parser("list", help="list configured + detected accounts")
    a_list.add_argument("--json", action="store_true", help="machine-readable output")

    a_add = acc_sub.add_parser("add", help="register an existing account's credentials")
    a_add.add_argument("name")
    a_add.add_argument("email")
    a_add.add_argument("password")
    a_add.add_argument("--verified", action="store_true", help="mark as verified")
    a_add.add_argument("--notes", default=None)

    a_import = acc_sub.add_parser("import", help="import MEGA remotes from an rclone config or credentials file")
    a_import.add_argument("--rclone-conf", type=str, default=None, help="path to an rclone.conf with type=mega remotes")
    a_import.add_argument("--credentials-file", type=str, default=None, help="text file, one '<email> <password>' per line")

    a_create = acc_sub.add_parser("create", help="sign up new MEGA accounts via plus-alias emails")
    a_create.add_argument("--count", type=int, default=1)
    a_create.add_argument("--base", dest="base", default=None, help="alias/name prefix (default: settings base_name)")
    a_create.add_argument("--email", default=None, help="override the generated plus-alias email")
    a_create.add_argument("--password", default=None, help="override the auto-generated password")
    a_create.add_argument("--display-name", default=None, help="MEGA account display name")

    a_verify = acc_sub.add_parser("verify", help="complete a signup with the link from MEGA's email")
    a_verify.add_argument("name")
    a_verify.add_argument("link")

    a_show = acc_sub.add_parser("show", help="show account details")
    a_show.add_argument("name")
    a_show.add_argument("--json", action="store_true")

    a_remove = acc_sub.add_parser("remove", help="remove an account from the registry and its rclone remote")
    a_remove.add_argument("name")

    p_refresh = sub.add_parser("refresh", help="login-refresh all (or named) accounts; cron-friendly exit code")
    p_refresh.add_argument("names", nargs="*")
    p_refresh.add_argument("--sleep", type=float, default=2.0, help="seconds between account logins (rate-limit guard)")
    p_refresh.add_argument(
        "--no-provision", dest="no_provision", action="store_true",
        help="skip (re)provisioning the rclone remote before probing",
    )

    p_df = sub.add_parser("df", help="storage usage per account")
    p_df.add_argument("names", nargs="*")

    p_ls = sub.add_parser("ls", help="list remote files")
    p_ls.add_argument("name")
    p_ls.add_argument("path", nargs="?", default="/")
    p_ls.add_argument("-R", "--recursive", action="store_true")
    p_ls.add_argument("--files", action="store_true")
    p_ls.add_argument("--dirs", action="store_true")

    p_mkdir = sub.add_parser("mkdir", help="create a remote directory")
    p_mkdir.add_argument("name")
    p_mkdir.add_argument("path")

    p_rm = sub.add_parser("rm", help="remove a remote file or directory")
    p_rm.add_argument("name")
    p_rm.add_argument("path")
    p_rm.add_argument("-r", "--recursive", action="store_true", help="required to remove directories")

    p_upload = sub.add_parser("upload", help="upload a local file or directory")
    p_upload.add_argument("name")
    p_upload.add_argument("local")
    p_upload.add_argument("path", help="remote destination path")

    p_download = sub.add_parser("download", help="download a remote file or directory")
    p_download.add_argument("name")
    p_download.add_argument("path", help="remote source path")
    p_download.add_argument("local")

    p_sync = sub.add_parser("sync", help="sync a local path with a remote path")
    p_sync.add_argument("name")
    p_sync.add_argument("local")
    p_sync.add_argument("path")
    p_sync.add_argument("--mode", choices=["push", "pull", "both"], default="push",
                        help="push=mirror local to remote, pull=mirror remote to local, both=two-way")

    p_sjobs = sub.add_parser("sync-jobs", help="run configured cron sync jobs")
    p_sjobs.add_argument("jobs", nargs="*")

    p_pair = sub.add_parser("pair", help="print a pairing code for the web GUI (WhatsApp-Web style)")
    p_pair.add_argument("--json", action="store_true", help="machine-readable output")

    p_connect = sub.add_parser(
        "connect",
        help="dial the web-GUI relay Worker and serve commands from the browser",
    )
    p_connect.add_argument("url", help="https://<your-worker>.workers.dev")
    p_connect.add_argument("--code", default=None, help="pairing code from `megamendung pair` (prompts otherwise)")
    p_connect.add_argument("--no-reconnect", action="store_true", help="exit on connection loss instead of retrying")
    p_connect.add_argument("--heartbeat", type=float, default=HEARTBEAT_SECONDS, help="seconds between keepalive pings (default 25)")

    p_compress = sub.add_parser("compress", help="compress images and/or videos in a local directory")
    p_compress.add_argument("path")
    p_compress.add_argument("--images", action="store_true")
    p_compress.add_argument("--videos", action="store_true")
    p_compress.add_argument("--quality", type=int, default=85, help="JPEG quality (default 85)")
    p_compress.add_argument("--max-dimension", type=int, default=None, help="downscale images larger than this many px")
    p_compress.add_argument("--preset", default="fast", help="ffmpeg x264 preset")
    p_compress.add_argument("--crf", type=int, default=23, help="ffmpeg CRF")
    p_compress.add_argument("--force", action="store_true", help="reprocess files even if already optimized")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    rclone_config_path = (
        Path(args.rclone_config).expanduser() if args.rclone_config else default_rclone_config_path()
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = Config.load(config_path)
    rclone = Rclone(config_path=rclone_config_path)

    handlers = {
        "config": cmd_config,
        "accounts": {
            "list": cmd_accounts_list,
            "add": cmd_accounts_add,
            "import": cmd_accounts_import,
            "create": cmd_accounts_create,
            "verify": cmd_accounts_verify,
            "show": cmd_accounts_show,
            "remove": cmd_accounts_remove,
        },
        "refresh": cmd_refresh,
        "df": cmd_df,
        "ls": cmd_ls,
        "mkdir": cmd_mkdir,
        "rm": cmd_rm,
        "upload": cmd_upload,
        "download": cmd_download,
        "sync": cmd_sync,
        "sync-jobs": cmd_sync_jobs,
        "pair": cmd_pair,
        "connect": cmd_connect,
        "compress": cmd_compress,
    }

    handler = handlers[args.command]
    try:
        if isinstance(handler, dict):
            sub_handler = handler[args.subcommand]
            return sub_handler(args, cfg, rclone)
        return handler(args, cfg, rclone)
    except ERRORS as exc:
        _err(str(exc))
        return 1
    except CliError as exc:
        _err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())