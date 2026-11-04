"""Render word-highlighted Arabic caption previews with the verified Mikhak Black font.

No network, upload or clip selection effects. Unverified or missing font/word
boundaries fail closed; silent font substitution and guessed highlights are banned.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import unicodedata
from pathlib import Path

FONT_NAME = 'Mikhak'
FONT_SIZE = 86
ASS_MARGIN_V = 300
MAX_CARD_CHARS = 34
MAX_CARD_WORDS = 5
MAX_CARD_SECONDS = 2.5
YELLOW = '&H0000FFFF&'  # ASS colours are BBGGRR
WHITE = '&H00FFFFFF&'


def _clean(value):
    """Strip styles and bidi override controls without rewriting spoken content."""
    text = re.sub(r'[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]', ' ', str(value))
    text = text.replace('\\', ' ').replace('{', '(').replace('}', ')').replace('\u0640', '')
    return unicodedata.normalize('NFC', re.sub(r'\s+', ' ', text)).strip()


def _seconds(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('CAPTION_TIMESTAMP_NOT_FINITE')
    return value


def _split_segment(cue):
    """Static captions for non-word-timed data; never claim estimated word timing."""
    words = cue['text'].split()
    chunks, current = [], []
    for word in words:
        if current and (len(' '.join(current + [word])) > MAX_CARD_CHARS or len(current) >= MAX_CARD_WORDS):
            chunks.append(' '.join(current))
            current = []
        current.append(word)
    if current:
        chunks.append(' '.join(current))
    if len(chunks) == 1:
        return [cue], False
    duration = cue['end']-cue['start']
    weights = [max(1, len(chunk)) for chunk in chunks]
    total = sum(weights)
    cursor, pieces = cue['start'], []
    for i, (text, weight) in enumerate(zip(chunks, weights)):
        right = cue['end'] if i == len(chunks)-1 else cursor + duration * weight / total
        pieces.append({'start': cursor, 'end': right, 'text': text, 'words': []})
        cursor = right
    return pieces, True


def build_caption_events(cues, start, end):
    """Return clip-relative caption cards with original word boundaries preserved.

    Segment-only data remains static and is flagged; never invent word-level timing.
    """
    start, end = _seconds(start), _seconds(end)
    if start < 0 or end <= start:
        raise ValueError('CAPTION_CLIP_RANGE_INVALID')
    observations = []
    for cue in cues:
        try:
            left, right = _seconds(cue['start']), _seconds(cue['end'])
            text = _clean(cue['text'])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or left < 0 or right <= left or right <= start or left >= end:
            continue
        left, right = max(left, start)-start, min(right, end)-start
        if right-left < .035:
            continue
        is_word = len(text.split()) == 1
        observations.append({'start': left, 'end': right, 'text': text,
                             'words': [{'start': left, 'end': right, 'text': text}] if is_word else []})
    observations.sort(key=lambda c: (c['start'], c['end']))
    if not observations:
        raise ValueError('CAPTION_NO_TIMED_SPEECH_IN_SELECTED_CLIP')

    expanded, approximated = [], False
    for cue in observations:
        if cue['words']:
            expanded.append(cue)
        else:
            pieces, approx = _split_segment(cue)
            expanded.extend(pieces)
            approximated |= approx
    cards = []
    for cue in expanded:
        prev = cards[-1] if cards else None
        can_merge = (prev is not None and len(prev['words']) > 0 and len(cue['words']) == 1
                     and cue['start'] - prev['end'] <= .40 and cue['start'] >= prev['start']
                     and cue['end'] - prev['start'] <= MAX_CARD_SECONDS
                     and len(prev['text']+' '+cue['text']) <= MAX_CARD_CHARS
                     and len(prev['words']) < MAX_CARD_WORDS)
        if can_merge:
            prev['text'] += ' '+cue['text']
            prev['end'] = max(prev['end'], cue['end'])
            prev['words'].extend(cue['words'])
        else:
            cards.append(dict(cue, words=list(cue['words'])))
    # Stop any card from carrying into the next one.
    for n, card in enumerate(cards[:-1]):
        if card['end'] > cards[n+1]['start']:
            card['end'] = cards[n+1]['start']
            card['words'] = [dict(w, start=max(w['start'], card['start']),
                                  end=min(w['end'], card['end'])) for w in card['words']
                             if min(w['end'], card['end'])-max(w['start'], card['start']) > .03]
    cards = [card for card in cards if card['end']-card['start'] >= .035]
    if not cards:
        raise ValueError('CAPTION_TOO_SHORT_AFTER_CLIP_TRIM')
    return cards, approximated


def _ass_stamp(t):
    cs = round(float(t)*100)
    hours, rem = divmod(cs, 360000)
    minutes, rem = divmod(rem, 6000)
    seconds, fract = divmod(rem, 100)
    return f'{hours}:{minutes:02}:{seconds:02}.{fract:02}'


def _styled_text(card, active_index):
    words = [w['text'] for w in card['words']] if card['words'] else card['text'].split()
    if active_index is None:
        return ' '.join(words)
    # libass splits right-to-left runs at style overrides. Put styled runs in
    # reverse RUN order (not reversed word order inside each run). Without this,
    # Arabic captions render with the words visibly reversed.
    before = ' '.join(words[:active_index])
    active = words[active_index]
    after = ' '.join(words[active_index+1:])
    runs = []
    if after:
        runs.append(after)
    runs.append(r'{\1c'+YELLOW+'}'+active+r'{\1c'+WHITE+'}')
    if before:
        runs.append(before)
    return ' '.join(runs)


def _dialogue_spans(card):
    """Exact word highlights; no ASS karaoke approximation or rewriting."""
    words = card['words']
    if not words:
        yield card['start'], card['end'], _styled_text(card, None)
        return
    bounds = sorted({round(v, 3) for v in [card['start'], card['end']] +
                     [max(card['start'], min(card['end'], w[t])) for w in words for t in ('start','end')]})
    spans = []
    for left, right in zip(bounds, bounds[1:]):
        if right-left < .015:
            continue
        mid = (left+right)/2
        active = next((i for i, w in enumerate(words) if w['start'] <= mid < w['end']), None)
        if spans and spans[-1][2] == active and abs(spans[-1][1]-left) < .003:
            spans[-1] = (spans[-1][0], right, active)
        else:
            spans.append((left, right, active))
    for left, right, active in spans:
        if round(right*100) > round(left*100):
            yield left, right, _styled_text(card, active)


def write_ass(events, destination, font_family=FONT_NAME):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if re.search(r'[,\r\n]', font_family):
        raise ValueError('CAPTION_FONT_FAMILY_INVALID')
    header = f'''[Script Info]
Title: Arabic Mikhak Black Word Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Arabic,{font_family},{FONT_SIZE},&H00FFFFFF,{YELLOW},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,5,2,2,65,65,{ASS_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = []
    for card in events:
        for left, right, caption_text in _dialogue_spans(card):
            lines.append('Dialogue: 0,{},{},Arabic,,0,0,0,,{}'.format(
                _ass_stamp(left), _ass_stamp(right), caption_text))
    if not lines:
        raise ValueError('CAPTION_EVENTS_EMPTY')
    path.write_text(header + '\n'.join(lines) + '\n', encoding='utf-8-sig')
    return path


def _verify_font(font_path, events):
    """Check exact font family+weight and every character we intend to burn."""
    from fontTools.ttLib import TTFont
    path = Path(font_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError('MIKHAK_BLACK_MISSING: run scripts/install_mikhak.py on the runner')
    font = TTFont(path)
    try:
        family = font['name'].getDebugName(1) or ''
        style = font['name'].getDebugName(2) or ''
        weight = font['OS/2'].usWeightClass
        if 'Mikhak' not in family or weight < 850 or 'Black' not in style:
            raise RuntimeError(f'MIKHAK_BLACK_IDENTITY_INVALID: {family}/{style}/{weight}')
        glyphs = font.getBestCmap() or {}
        content = ''.join(c['text'] for c in events)
        absent = sorted({ch for ch in content if not ch.isspace() and ord(ch) not in glyphs})
        if absent:
            raise RuntimeError('MIKHAK_UNSUPPORTED_CHARACTERS: '+', '.join(f'U+{ord(ch):04X}' for ch in absent))
        matched = subprocess.run(['fc-match', '-f', '%{file}', f'{family}:style=Black'],
                                 capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if Path(matched).resolve() != path:
            raise RuntimeError('MIKHAK_FONT_FALLBACK_DETECTED: fontconfig did not select installed Mikhak Black')
        return family, path
    finally:
        font.close()


def burn_captions(input_video, cues, start, end, output_video='output_reel_captioned.mp4',
                  subtitle_file='preview_captions.ass', *, strict_word_timing=True):
    """Burn only with real word times and Mikhak Black. Never send uncaptioned output."""
    input_video, output_video = Path(input_video), Path(output_video)
    if not input_video.is_file() or input_video.stat().st_size < 1000:
        raise RuntimeError('CAPTION_SOURCE_MP4_INVALID')
    if input_video.resolve() == output_video.resolve():
        raise ValueError('CAPTION_OUTPUT_MUST_DIFFER_FROM_INPUT')
    events, approximated = build_caption_events(cues, start, end)
    if strict_word_timing and (approximated or any(not card['words'] for card in events)):
        raise RuntimeError('CAPTION_WORD_TIMESTAMPS_REQUIRED: provider returned segments instead of word timings')
    font_path = os.getenv('MIKHAK_FONT_PATH', str(Path.home()/'.local/share/fonts/yt-agent/Mikhak-Black.ttf'))
    family, actual_font = _verify_font(font_path, events)
    ass_path = write_ass(events, subtitle_file, font_family=family)
    if output_video.exists():
        output_video.unlink()
    # CI uses Linux; font path and subtitle path have no ffmpeg filter delimiters.
    for p in (str(ass_path), str(actual_font.parent)):
        if any(x in p for x in (';', ':', ',', '[', ']')):
            raise ValueError('CAPTION_PATH_UNSUPPORTED_FFMPEG_FILTER_CHAR')
    filter_arg = f'ass={ass_path.as_posix()}:fontsdir={actual_font.parent.as_posix()}:shaping=complex'
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(input_video),
               '-vf', filter_arg, '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
               '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', str(output_video)]
    subprocess.run(command, check=True, timeout=900)
    if not output_video.is_file() or output_video.stat().st_size < 10000:
        raise RuntimeError('CAPTION_VIDEO_OUTPUT_MISSING')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1', str(output_video)],
                           capture_output=True, text=True, check=True, timeout=30)
    duration = float(probe.stdout.strip())
    if not 0 < duration <= end-start+.8:
        raise RuntimeError('CAPTION_OUTPUT_DURATION_INVALID')
    highlights = sum(bool(c['words']) for c in events)
    print(f'CAPTIONS_BURNED: YES; FONT: {family} Black; HIGHLIGHT_CARDS: {highlights}; '
          f'ESTIMATED_WORD_TIMING: NO; DURATION: {duration:.2f}s')
    return str(output_video)
