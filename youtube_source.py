
"""YouTube acquisition with fallback clients and explicit error handling."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


class YouTubeSourceError(RuntimeError):
    """Safe diagnostic for GitHub Actions and Telegram."""


def _classify_error(output: str) -> str:
    text = output.lower()

    if any(marker in text for marker in (
        'sign in to confirm',
        'not a bot',
        'this helps protect our community',
        'cookies for the authentication',
        'confirm your age',
        'login required',
        'login to confirm',
        'authentication required',
    )):
        return 'YOUTUBE_AUTH_REQUIRED'

    if any(marker in text for marker in (
        'video unavailable',
        'private video',
        'members-only',
        'this video is not available',
        'not available in your country',
    )):
        return 'YOUTUBE_VIDEO_UNAVAILABLE'

    if any(marker in text for marker in (
        'http error 403',
        '403 forbidden',
        'po token',
        'potoken',
        'http error 429',
        'too many requests',
    )):
        return 'YOUTUBE_ACCESS_RESTRICTED'

    if any(marker in text for marker in (
        'no supported javascript runtime',
        'yt-dlp-ejs',
        'javascript challenge solving failed',
        'challenge solver failed',
        'ejs',
    )):
        return 'YOUTUBE_EJS_UNAVAILABLE'

    if any(marker in text for marker in (
        'requested format is not available',
        'requested format not available',
    )):
        return 'YOUTUBE_FORMAT_UNAVAILABLE'

    if any(marker in text for marker in (
        'timed out',
        'connection reset',
        'connection aborted',
        'network is unreachable',
        'temporary failure',
        'http error 502',
        'http error 503',
        'http error 504',
    )):
        return 'YOUTUBE_NETWORK_TEMPORARY'

    return 'YOUTUBE_DOWNLOAD_FAILED'


def _base_command(cookies_file=None, *, player_clients=None):
    cmd = [
        'yt-dlp',
        '--no-playlist',
        '--no-progress',
        '--retries', '3',
        '--fragment-retries', '3',
        '--extractor-retries', '3',
        '--socket-timeout', '25',
    ]

    if cookies_file:
        if not Path(cookies_file).is_file():
            raise YouTubeSourceError(
                'YOUTUBE_COOKIES_FILE_MISSING'
            )
        cmd += ['--cookies', str(cookies_file)]

    if player_clients:
        cmd += [
            '--extractor-args',
            'youtube:player_client=' + player_clients,
        ]

    return cmd


def _run_download(cmd: list[str], *, timeout=900, attempts=2):
    """Retry transient errors only."""
    last_code = 'YOUTUBE_DOWNLOAD_FAILED'

    for attempt in range(attempts):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise YouTubeSourceError(
                'YTDLP_NOT_INSTALLED'
            ) from exc
        except subprocess.TimeoutExpired:
            last_code = 'YOUTUBE_DOWNLOAD_TIMEOUT'
        else:
            if result.returncode == 0:
                return

            last_code = _classify_error(
                (result.stderr or '') +
                '\n' +
                (result.stdout or '')
            )

            if last_code != 'YOUTUBE_NETWORK_TEMPORARY':
                raise YouTubeSourceError(last_code)

        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))

    raise YouTubeSourceError(last_code)


def _existing_media(
    prefix: Path,
    extensions: set[str],
) -> Path:

    files = [
        p for p in prefix.parent.glob(prefix.name + '.*')
        if p.is_file()
        and p.suffix.lower() in extensions
        and p.stat().st_size > 0
    ]

    if not files:
        kind = (
            'AUDIO'
            if extensions == {'.mp3'}
            else 'VIDEO'
        )
        raise YouTubeSourceError(
            'YOUTUBE_' + kind + '_MISSING'
        )

    return sorted(
        files,
        key=lambda p: (
            p.suffix.lower() != '.mp4',
            p.name,
        ),
    )[0]


def _download_profiles(cookies_file):
    """Default extraction, then public client fallback.

    No account cookies are sent with alternate clients.
    IP blocks may still require a separate network route.
    """
    return (
        (cookies_file, None),
        (None, 'android_vr,web_embedded'),
    )


def download_youtube_audio(
    url: str,
    cookies_file=None,
    *,
    directory='youtube_audio',
) -> Path:
    """Download source audio for /prepare."""

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)

    prefix = root / 'source'
    target = root / 'source.mp3'

    if target.is_file() and target.stat().st_size > 0:
        return target

    last_error = None

    for cookies, clients in _download_profiles(cookies_file):
        cmd = _base_command(
            cookies,
            player_clients=clients,
        ) + [
            '--format', 'ba/b',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '5',
            '--output', str(prefix) + '.%(ext)s',
            url,
        ]

        try:
            _run_download(cmd)
            return _existing_media(prefix, {'.mp3'})

        except YouTubeSourceError as exc:
            last_error = exc

            if (
                str(exc) not in (
                    'YOUTUBE_AUTH_REQUIRED',
                    'YOUTUBE_ACCESS_RESTRICTED',
                )
                or clients
            ):
                raise

            print(
                'YOUTUBE_DOWNLOAD: DEFAULT_CLIENT_BLOCKED; '
                'TRYING_PUBLIC_CLIENTS'
            )

    raise last_error or YouTubeSourceError(
        'YOUTUBE_DOWNLOAD_FAILED'
    )


def download_youtube_video(
    url: str,
    cookies_file=None,
    *,
    directory='.',
    stem='input',
) -> Path:
    """Download source video for /clip."""

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)

    prefix = root / stem
    extensions = {'.mp4', '.mkv', '.webm', '.mov'}

    existing = [
        p for p in root.glob(stem + '.*')
        if p.is_file()
        and p.suffix.lower() in extensions
    ]

    if existing:
        raise YouTubeSourceError(
            'YOUTUBE_STALE_INPUT_FILE: '
            'source file already exists'
        )

    formats = (
        'bv*[height<=1080]+ba/b[height<=1080]/b',
        'bv*+ba/b',
    )

    last_error = None

    for cookies, clients in _download_profiles(cookies_file):
        for index, fmt in enumerate(formats):
            cmd = _base_command(
                cookies,
                player_clients=clients,
            ) + [
                '--merge-output-format', 'mp4',
                '--format', fmt,
                '--output', str(prefix) + '.%(ext)s',
                url,
            ]

            try:
                _run_download(cmd)
                return _existing_media(
                    prefix,
                    extensions,
                )

            except YouTubeSourceError as exc:
                last_error = exc

                if (
                    str(exc) == 'YOUTUBE_FORMAT_UNAVAILABLE'
                    and index + 1 < len(formats)
                ):
                    continue

                if (
                    str(exc) in (
                        'YOUTUBE_AUTH_REQUIRED',
                        'YOUTUBE_ACCESS_RESTRICTED',
                    )
                    and not clients
                ):
                    print(
                        'YOUTUBE_DOWNLOAD: DEFAULT_CLIENT_BLOCKED; '
                        'TRYING_PUBLIC_CLIENTS'
                    )
                    break

                raise

    raise last_error or YouTubeSourceError(
        'YOUTUBE_DOWNLOAD_FAILED'
    )
