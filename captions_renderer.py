"""Burn legible, synchronized Arabic captions into an APPROVED clip preview.

Source cues always come from real timed speech recognition. Word-level timings are
preferred; longer segment-only results may require approximate sub-segment timing.
No publishing, network calls, or video selection are performed here.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

FONT_NAME = 'Noto Sans Arabic'
# Keep away from Shorts UI at the bottom; image is 1080x1920.
ASS_MARGIN_V = 285
MAX_CARD_CHARS = 51
MAX_CARD_SECONDS = 3.9


def _clean(value):
    """Never let a transcription inject ASS style overrides or hidden controls."""
    value = re.sub(r'[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]', ' ', str(value))
    value = value.replace('\\', ' ').replace('{', '(').replace('}', ')')
    return re.sub(r'\s+', ' ', value).strip()


def _seconds(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('CAPTION_TIMESTAMP_NOT_FINITE')
    return value


def _split_long_cue(cue):
    """Split *segment* captions into readable cards; duration within a segment is estimated."""
    words = cue['text'].split()
    if len(cue['text']) <= MAX_CARD_CHARS and cue['end']-cue['start'] <= MAX_CARD_SECONDS + 0.6:
        return [cue], False
    chunks = []
    current = []
    for word in words:
        if current and len(' '.join(current + [word])) > MAX_CARD_CHARS:
            chunks.append(' '.join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(' '.join(current))
    if len(chunks) == 1:
        # Even a short phrase can be long-lasting; leave timing faithful to segment.
        return [cue], False
    length = cue['end'] - cue['start']
    weights = [max(1, len(text)) for text in chunks]
    total = sum(weights)
    pieces, cursor = [], cue['start']
    for n, (text, weight) in enumerate(zip(chunks, weights)):
        end = cue['end'] if n == len(chunks) - 1 else cursor + length * weight / total
        pieces.append({'start': cursor, 'end': end, 'text': text})
        cursor = end
    return pieces, True


def build_caption_events(cues, start, end):
    """Output (relative-time events, whether any sub-segment times are estimated)."""
    start, end = _seconds(start), _seconds(end)
    if start < 0 or end <= start:
        raise ValueError('CAPTION_CLIP_RANGE_INVALID')
    ordered = []
    for cue in cues:
        try:
            left, right = _seconds(cue['start']), _seconds(cue['end'])
            text = _clean(cue['text'])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or left < 0 or right <= left or left >= end or right <= start:
            continue
        ordered.append({'start': max(start, left) - start,
                        'end': min(end, right) - start, 'text': text})
    ordered.sort(key=lambda item: (item['start'], item['end']))
    if not ordered:
        raise ValueError('CAPTION_NO_TIMED_SPEECH_IN_SELECTED_CLIP')
    expanded = []
    estimated = False
    for cue in ordered:
        pieces, approximate = _split_long_cue(cue)
        expanded.extend(pieces)
        estimated |= approximate
    # Merge adjacent word-level observations into short caption cards.
    events = []
    for cue in expanded:
        if events and (cue['start'] - events[-1]['end'] <= 0.45 and
                       cue['start'] >= events[-1]['start'] and
                       max(events[-1]['end'], cue['end'])-events[-1]['start'] <= MAX_CARD_SECONDS and
                       len(events[-1]['text'] + ' ' + cue['text']) <= MAX_CARD_CHARS):
            prev = events[-1]
            prev['text'] += ' ' + cue['text']
            prev['end'] = max(prev['end'], cue['end'])
        else:
            events.append(dict(cue))
    # ASS overlays cannot overlap or leak outside the selected source interval.
    result = []
    for idx, cue in enumerate(events):
        left = max(0, cue['start'])
        right = min(end-start, cue['end'])
        if idx + 1 < len(events):
            right = min(right, events[idx+1]['start'])
        if right-left >= 0.08:
            result.append({'start': left, 'end': right, 'text': cue['text']})
    if not result:
        raise ValueError('CAPTION_TOO_SHORT_AFTER_CLIP_TRIM')
    return result, estimated


def _ass_stamp(value):
    centiseconds = round(value * 100)
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    seconds, cs = divmod(rem, 100)
    return f'{hours}:{minutes:02}:{seconds:02}.{cs:02}'


def _two_lines(text):
    words, lines = text.split(), ['']
    for word in words:
        current = lines[-1]
        if current and len(current) + 1 + len(word) > 26 and len(lines) == 1:
            lines.append(word)
        else:
            lines[-1] = (current + ' ' + word).strip()
    return r'\N'.join(lines)


def write_ass(events, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = '''[Script Info]
Title: Arabic Captions — Clip Preview
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Arabic,Noto Sans Arabic,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,2,80,80,285,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = []
    for event in events:
        lines.append('Dialogue: 0,{},{},Arabic,,0,0,0,,{}'.format(
            _ass_stamp(event['start']), _ass_stamp(event['end']), _two_lines(event['text'])))
    path.write_text(header + '\n'.join(lines) + '\n', encoding='utf-8-sig')
    return path


def burn_captions(input_video, cues, start, end, output_video='output_reel_captioned.mp4',
                  subtitle_file='preview_captions.ass'):
    """Fail closed: only return when a captioned output passes ffprobe validation."""
    input_video, output_video = Path(input_video), Path(output_video)
    if not input_video.is_file() or input_video.stat().st_size < 1000:
        raise RuntimeError('CAPTION_SOURCE_MP4_INVALID')
    if input_video.resolve() == output_video.resolve():
        raise ValueError('CAPTION_OUTPUT_MUST_DIFFER_FROM_INPUT')
    events, estimated = build_caption_events(cues, start, end)
    ass_path = write_ass(events, subtitle_file)
    if output_video.exists():
        output_video.unlink()
    # Files are local fixed names without colon/commas to avoid ffmpeg filter escaping.
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(input_video),
               '-vf', 'ass=' + str(ass_path).replace('\\', '/'),
               '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-pix_fmt', 'yuv420p',
               '-c:a', 'copy', '-movflags', '+faststart', str(output_video)]
    subprocess.run(command, check=True, timeout=900)
    if not output_video.is_file() or output_video.stat().st_size < 10000:
        raise RuntimeError('CAPTION_VIDEO_OUTPUT_MISSING')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1', str(output_video)],
                           capture_output=True, text=True, check=True, timeout=30)
    duration = float(probe.stdout.strip())
    if not 0 < duration <= end - start + 0.8:
        raise RuntimeError('CAPTION_OUTPUT_DURATION_INVALID')
    print(f'CAPTIONS_BURNED: YES; EVENTS: {len(events)}; ESTIMATED_SUBSEGMENT_TIMING: {estimated}; DURATION: {duration:.2f}s')
    return str(output_video)
