
"""YouTube media acquisition for the bot (no publishing or credential side effects).

Supports public YouTube videos through yt-dlp's currently supported EJS runtime;
optional Netscape-format cookies may help with content the operator can access.
YouTube itself can still block hosting-provider IPs or require PO tokens.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


class YouTubeSourceError(RuntimeError):
    """A public, non-sensitive diagnostic intended for GitHub/Telegram logs."""


def _classify_error(output: str) -> str:
    text = output.lower()
    if any(marker in text for marker in (
        'sign in to confirm', 'not a bot', 'this helps protect our community',
        'cookies for the authentication', 'confirm your age', 'login required',
        'login to confirm', 'authentication required',
    )):
        return 'YOUTUBE_AUTH_REQUIRED'
    if any(marker in text for marker in (
        'video unavailable', 'private video', 'members-only',
        'this video is not available', 'not available in your country'
    )):
        return 'YOUTUBE_VIDEO_UNAVAILABLE'
    if any(marker in text for marker in (
        'http error 403', '403 forbidden', 'po token', 'potoken',
        'http error 429', 'too many requests'
    )):
        return 'YOUTUBE_ACCESS_RESTRICTED'
    if any(marker in text for marker in (
        'no supported javascript runtime', 'yt-dlp-ejs',
        'javascript challenge solving failed',
        'challenge solver failed', 'ejs'
    )):
        return 'YOUTUBE_EJS_UNAVAILABLE'
    if (
        'requested format is not available' in text
        or 'requested format not available' in text
    ):
        return 'YOUTUBE_FORMAT_UNAVAILABLE'
    if any(marker in text for marker in (
        'timed out', 'connection reset', 'connection aborted',
        'network is unreachable', 'temporary failure',
        'http error 502', 'http error 503', 'http error 504'
    )):
        return 'YOUTUBE_NETWORK_TEMPORARY'
    return 'YOUTUBE_DOWNLOAD_FAILED'


def _base_command(cookies_file=None):
    cmd = [
        'yt-dlp', '--no-playlist', '--no-progress',
        '--retries', '3', '--fragment-retries', '3',
        '--extractor-retries', '3', '--socket-timeout', '25'
    ]
    if cookies_file:
        if not Path(cookies_file).is_file():
            raise YouTubeSourceError('YOUTUBE_COOKIES_FILE_MISSING')
        cmd += ['--cookies', str(cookies_file)]
    return cmd


def _run_download(cmd: list[str], *, timeout=900, attempts=2):
    """Retry only transient failures; don't reattempt authentication/403 blocks."""
    last_code = 'YOUTUBE_DOWNLOAD_FAILED'
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except FileNotFoundError as exc:
            raise YouTubeSourceError('YTDLP_NOT_INSTALLED') from exc
        except subprocess.TimeoutExpired:
            last_code = 'YOUTUBE_DOWNLOAD_TIMEOUT'
        else:
            if result.returncode == 0:
                return
            last_code = _classify_error(
                (result.stderr or '') + '\n' + (result.stdout or '')
            )
            if last_code != 'YOUTUBE_NETWORK_TEMPORARY':
                raise YouTubeSourceError(last_code)
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise YouTubeSourceError(last_code)


def _existing_media(prefix: Path, extensions: set[str]) -> Path:
    # yt-dlp writes the requested template prefix with a container suffix.
    files = [
        p for p in prefix.parent.glob(prefix.name + '.*')
        if p.is_file()
        and p.suffix.lower() in extensions
        and p.stat().st_size > 0
    ]
    if not files:
        raise YouTubeSourceError(
            'YOUTUBE_' +
            ('AUDIO' if extensions == {'.mp3'} else 'VIDEO') +
            '_MISSING'
        )
    return sorted(
        files,
        key=lambda p: (p.suffix.lower() != '.mp4', p.name)
    )[0]


def download_youtube_audio(
    url: str,
    cookies_file=None,
    *,
    directory='youtube_audio'
) -> Path:
    """Fetch audio for /prepare when YouTube does not offer usable timed subtitles."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    prefix = root / 'source'
    target = root / 'source.mp3'
    if target.is_file() and target.stat().st_size > 0:
        return target

    cmd = _base_command(cookies_file) + [
        '--format', 'ba/b',
        '--extract-audio',
        '--audio-format', 'mp3',
        '--audio-quality', '5',
        '--output', str(prefix) + '.%(ext)s',
        url,
    ]
    _run_download(cmd)
    return _existing_media(prefix, {'.mp3'})


def download_youtube_video(
    url: str,
    cookies_file=None,
    *,
    directory='.',
    stem='input'
) -> Path:
    """Download an actual source video for /clip; avoid stale leftovers from earlier runs."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    prefix = root / stem
    extensions = {'.mp4', '.mkv', '.webm', '.mov'}
    existing = [
        p for p in root.glob(stem + '.*')
        if p.is_file() and p.suffix.lower() in extensions
    ]
    if existing:
        raise YouTubeSourceError(
            'YOUTUBE_STALE_INPUT_FILE: source file already exists'
        )

    formats = (
        'bv*[height<=1080]+ba/b[height<=1080]/b',
        'bv*+ba/b'
    )
    for index, fmt in enumerate(formats):
        cmd = _base_command(cookies_file) + [
            '--merge-output-format', 'mp4',
            '--format', fmt,
            '--output', str(prefix) + '.%(ext)s',
            url,
        ]
        try:
            _run_download(cmd)
            return _existing_media(prefix, extensions)
        except YouTubeSourceError as error:
            if (
                str(error) != 'YOUTUBE_FORMAT_UNAVAILABLE'
                or index + 1 == len(formats)
            ):
                raise

    raise YouTubeSourceError('YOUTUBE_DOWNLOAD_FAILED')
