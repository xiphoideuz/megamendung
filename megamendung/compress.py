"""Local image + video compression (ported from legacy mega_manager).

Images are re-encoded with Pillow (JPEG quality / PNG optimize), videos are
re-encoded with ffmpeg to H.264 MP4. Files are only replaced when the result
is strictly smaller. A small JSON state file under the config dir remembers
already-optimized files (keyed by size+mtime) so cron reruns are cheap.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".flv", ".m4v", ".mkv", ".mp4", ".mpeg", ".mpg", ".wmv", ".mov", ".webm"}
SKIP_MARKERS = ("compressimages-backup", "unoptimized", ".megamendung", "~")


class CompressionError(Exception):
    """Raised when a compression job can not proceed."""


class CompressResult:
    def __init__(self) -> None:
        self.images_done = 0
        self.videos_done = 0
        self.images_failed = 0
        self.videos_failed = 0
        self.saved_bytes = 0
        self.processed = 0
        self.skipped = 0

    def merge(self, other: "CompressResult") -> None:
        for attr in ("images_done", "videos_done", "images_failed", "videos_failed",
                     "saved_bytes", "processed", "skipped"):
            setattr(self, attr, getattr(self, attr) + getattr(other, attr))


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {"images": {}, "videos": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {"images": {}, "videos": {}}
        self.data.setdefault("images", {})
        self.data.setdefault("videos", {})

    def _fingerprint(self, path: Path) -> str:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"

    def already_done(self, kind: str, path: Path) -> bool:
        return self.data[kind].get(str(path)) == self._fingerprint(path)

    def mark(self, kind: str, path: Path) -> None:
        self.data[kind][str(path)] = self._fingerprint(path)

    def drop(self, kind: str, path: Path) -> None:
        self.data[kind].pop(str(path), None)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)


def _should_skip(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in SKIP_MARKERS) or lower.startswith(".")


def compress_images(
    directory: Path,
    *,
    state: StateStore,
    extensions: set[str] | None = None,
    quality: int = 85,
    max_dimension: int | None = None,
    force: bool = False,
) -> CompressResult:
    from PIL import Image

    result = CompressResult()
    exts = {e if e.startswith(".") else f".{e}" for e in (extensions or IMAGE_EXTENSIONS)}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if _should_skip(name):
                continue
            ext = Path(name).suffix.lower()
            if ext not in exts:
                continue
            path = Path(root) / name
            result.processed += 1
            if not force and state.already_done("images", path):
                result.skipped += 1
                continue
            try:
                with Image.open(path) as img:
                    work = img.convert("RGB") if img.mode not in ("RGB", "RGBA", "L", "P") else img
                    if max_dimension and max(work.size) > max_dimension:
                        scale = max_dimension / float(max(work.size))
                        work = work.resize(
                            (max(1, int(work.width * scale)), max(1, int(work.height * scale))),
                            Image.LANCZOS,
                        )
                    fd, tmp_name = tempfile.mkstemp(prefix=".megamendung_", suffix=ext, dir=str(path.parent))
                    os.close(fd)
                    try:
                        if ext in (".jpg", ".jpeg", ".webp"):
                            work.save(tmp_name, quality=quality, optimize=True)
                        else:
                            work.save(tmp_name, optimize=True)
                        new_size = os.path.getsize(tmp_name)
                        old_size = os.path.getsize(path)
                        if new_size < old_size:
                            shutil.copymode(path, tmp_name)
                            os.replace(tmp_name, path)
                            result.saved_bytes += old_size - new_size
                            result.images_done += 1
                            state.mark("images", path)
                        else:
                            os.unlink(tmp_name)
                            state.mark("images", path)
                    finally:
                        if os.path.exists(tmp_name):
                            os.unlink(tmp_name)
            except Exception:
                result.images_failed += 1
                state.drop("images", path)
    state.save()
    return result


def _ffmpeg(args: list[str], timeout: float = 3600.0) -> None:
    if shutil.which("ffmpeg") is None:
        raise CompressionError("ffmpeg not found on PATH; install it or skip video compression")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise CompressionError(f"ffmpeg failed: {proc.stderr.strip()}")


def compress_videos(
    directory: Path,
    *,
    state: StateStore,
    extensions: set[str] | None = None,
    preset: str = "fast",
    crf: int = 23,
    force: bool = False,
) -> CompressResult:
    result = CompressResult()
    exts = {e if e.startswith(".") else f".{e}" for e in (extensions or VIDEO_EXTENSIONS)}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if _should_skip(name):
                continue
            ext = Path(name).suffix.lower()
            if ext not in exts:
                continue
            path = Path(root) / name
            result.processed += 1
            if not force and state.already_done("videos", path):
                result.skipped += 1
                continue
            out_tmp = path.with_name(f"{path.stem}.megamendung_compressed.mp4")
            try:
                _ffmpeg(
                    [
                        "-i", str(path),
                        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                        "-c:a", "aac", "-movflags", "+faststart",
                        str(out_tmp),
                    ]
                )
                if out_tmp.exists():
                    new_size = os.path.getsize(out_tmp)
                    old_size = os.path.getsize(path)
                    if new_size < old_size:
                        shutil.copymode(path, out_tmp)
                        os.replace(out_tmp, path)
                        result.saved_bytes += old_size - new_size
                        result.videos_done += 1
                    else:
                        os.unlink(out_tmp)
                    state.mark("videos", path)
                else:
                    result.videos_failed += 1
                    state.drop("videos", path)
            except CompressionError:
                result.videos_failed += 1
                state.drop("videos", path)
                if out_tmp.exists():
                    os.unlink(out_tmp)
    state.save()
    return result