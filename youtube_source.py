
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

            # Log only the classified reason, never stderr: yt-dlp output may
            # contain signed URLs, cookie values or other sensitive data.
            print(f'YOUTUBE_DOWNLOAD_DIAGNOSTIC: CODE={last_code}; EXIT={result.returncode}; ATTEMPT={attempt + 1}/{attempts}')
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


_FALLBACK_ERRORS = {
    'YOUTUBE_AUTH_REQUIRED',
    'YOUTUBE_ACCESS_RESTRICTED',
    'YOUTUBE_FORMAT_UNAVAILABLE',
}


def _download_profiles(cookies_file):
    """Try independent public YouTube player clients after the default.

    Alternate clients receive no account cookies. A network-level/IP block
    cannot be solved by merely changing the player client.
    """
    return (
        (None, 'mweb'),
        (cookies_file, None),
        (None, 'android_vr'),
        (None, 'web_embedded'),
        (None, 'web_safari'),
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

    profiles = _download_profiles(cookies_file)
    print(f'YOUTUBE_AUDIO_ACCESS: PROFILES={len(profiles)}; '
          f'COOKIES_CONFIGURED={bool(cookies_file)}; '
          'SOURCE=YOUTUBE; ROUTE=YTDLP_CONFIG')
    for index, (cookies, clients) in enumerate(profiles):
        print(f'YOUTUBE_AUDIO_PROFILE: INDEX={index + 1}/{len(profiles)}; '
              f'CLIENT={clients or "default"}; COOKIES={"yes" if cookies else "no"}')
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
            if str(exc) not in _FALLBACK_ERRORS or index + 1 == len(profiles):
                raise
            print(
                'YOUTUBE_DOWNLOAD: AUDIO_PROFILE_FAILED: '
                + str(exc) + '; TRYING_NEXT_PUBLIC_CLIENT'
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

    profiles = _download_profiles(cookies_file)
    for profile_index, (cookies, clients) in enumerate(profiles):
        print(f'YOUTUBE_VIDEO_PROFILE: INDEX={profile_index + 1}/{len(profiles)}; CLIENT={clients or "default"}; COOKIES={"yes" if cookies else "no"}')
        for format_index, fmt in enumerate(formats):
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
                if str(exc) == 'YOUTUBE_FORMAT_UNAVAILABLE' and format_index + 1 < len(formats):
                    continue
                if str(exc) not in _FALLBACK_ERRORS or profile_index + 1 == len(profiles):
                    raise
                print(
                    'YOUTUBE_DOWNLOAD: VIDEO_PROFILE_FAILED: '
                    + str(exc) + '; TRYING_NEXT_PUBLIC_CLIENT'
                )
                break

    raise last_error or YouTubeSourceError(
        'YOUTUBE_DOWNLOAD_FAILED'
    )
