"""Download a public Google Drive video share link for processing."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import gdown

_MAX_BYTES = 2 * 1024 * 1024 * 1024
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{15,}$")
_FILE_PATH = re.compile(r"^/file/d/([A-Za-z0-9_-]+)(?:/|$)")


def is_drive_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in (
            "drive.google.com", "www.drive.google.com"
        )
    except (ValueError, TypeError):
        return False


def _validate_link(url: str) -> None:
    if not is_drive_url(url):
        raise ValueError("DRIVE_LINK_INVALID: use a Google Drive file link")
    parsed = urlparse(url)
    match = _FILE_PATH.match(parsed.path)
    if match:
        file_id = match.group(1)
    elif parsed.path in ("/open", "/uc"):
        file_id = parse_qs(parsed.query).get("id", [""])[0]
    else:
        raise ValueError("DRIVE_LINK_INVALID: link must point to one video file")
    if not _FILE_ID.fullmatch(file_id):
        raise ValueError("DRIVE_LINK_INVALID: invalid file identifier")


def download_drive_video(url: str, target: str = "drive_source.mp4") -> Path:
    """Only supports share links accessible without Google sign-in."""
    _validate_link(url)
    path = Path(target)
    if path.exists():
        raise RuntimeError("DRIVE_SOURCE_ALREADY_EXISTS")

    def check_size(downloaded, total):
        if downloaded > _MAX_BYTES or (total is not None and total > _MAX_BYTES):
            raise RuntimeError("DRIVE_VIDEO_TOO_LARGE: maximum 2 GiB")

    try:
        result = gdown.download(
            url=url, output=str(path), quiet=True, use_cookies=False,
            timeout=(25, 120), retries=2, progress=check_size,
        )
        if result is None or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("DRIVE_DOWNLOAD_UNAVAILABLE: check link sharing and downloads")
        if path.stat().st_size > _MAX_BYTES:
            raise RuntimeError("DRIVE_VIDEO_TOO_LARGE: maximum 2 GiB")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1",
             str(path)],
            capture_output=True, text=True, timeout=45,
        )
        if probe.returncode != 0 or "video" not in probe.stdout:
            raise RuntimeError("DRIVE_SOURCE_NOT_VIDEO: file must contain video")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    print("SOURCE: GOOGLE_DRIVE_VIDEO; VALIDATED: YES; YOUTUBE_DOWNLOAD: SKIPPED")
    return path
