"""Install Mikhak Black from the type designer's repository; no bundled font.

The font file is downloaded by GitHub Actions, not committed to the user's code.
If the upstream is inaccessible or malformed, stop rather than silently fall back.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

import requests
from fontTools.ttLib import TTFont

UPSTREAM = 'https://raw.githubusercontent.com/aminabedi68/Mikhak/0b52f9249e7978a4e74c5785c1116694389e9e64/fonts/ttf/Mikhak-Black.ttf'
TARGET = Path.home()/'.local/share/fonts/yt-agent/Mikhak-Black.ttf'
# Standard Arabic + common hamzas, tashkeel, digits, punctuation, Latin/vidIQ numerals.
REQUIRED = ('ءآأؤإئابتثجحخدذرزسشصضطظعغفقكلمنهويىة'
            'ًٌٍَُِّْ٠١٢٣٤٥٦٧٨٩0123456789%،؟.():-'
            'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')


def validate_font(path):
    if not path.is_file() or path.stat().st_size < 10000:
        raise RuntimeError('MIKHAK_BLACK_DOWNLOAD_INVALID')
    font = TTFont(path)
    try:
        family = font['name'].getDebugName(1) or ''
        style = font['name'].getDebugName(2) or ''
        weight = font['OS/2'].usWeightClass
        if 'Mikhak' not in family or 'Black' not in style or weight < 850:
            raise RuntimeError(f'WRONG_FONT: {family}/{style}/{weight}')
        available = font.getBestCmap() or {}
        missing = sorted({ch for ch in REQUIRED if ord(ch) not in available})
        if missing:
            raise RuntimeError('MIKHAK_COMMON_ARABIC_GLYPHS_MISSING: '+', '.join('U+%04X'%ord(c) for c in missing))
        return family
    finally:
        font.close()


def install(source=None, destination=TARGET):
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source:
        data = Path(source).read_bytes()
    else:
        for attempt in range(3):
            try:
                response = requests.get(UPSTREAM, timeout=(10, 45))
                response.raise_for_status()
                data = response.content
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    tmp = destination.with_suffix('.tmp')
    try:
        tmp.write_bytes(data)
        family = validate_font(tmp)
        tmp.replace(destination)
        subprocess.run(['fc-cache', '-f', str(destination.parent)], check=True, timeout=60,
                       capture_output=True, text=True)
        candidate = subprocess.run(['fc-match', '-f', '%{file}', f'{family}:style=Black'],
                                   capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if Path(candidate).resolve() != destination.resolve():
            raise RuntimeError('MIKHAK_FONTCONFIG_FALLBACK_DETECTED')
    except Exception:
        tmp.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    print(f'MIKHAK_BLACK_READY: YES; FONT_FAMILY: {family}; FONT_FILE: {destination}; REQUIRED_CHARS: {len(REQUIRED)}')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', help='Use an existing authentic Mikhak Black TTF for offline testing')
    parser.add_argument('--destination', default=str(TARGET))
    args = parser.parse_args()
    install(args.source, args.destination)
