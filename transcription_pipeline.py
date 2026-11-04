"""Timestamp-grounded transcript acquisition. No publishing side effects."""
import json
import os
import re
import subprocess
from pathlib import Path
import requests


def _stamp(value):
    parts = value.replace(',', '.').split(':')
    try:
        result = 0.0
        for part in parts:
            result = result * 60 + float(part)
        return result
    except ValueError:
        return None


def parse_vtt(raw):
    """Parse VTT cues, removing inline timestamps and duplicate rolling captions."""
    raw = re.sub(r'<[^>]*>', '', raw.replace('\ufeff', ''))
    cues = []
    for block in re.split(r'\n\s*\n', raw):
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        timing = next((i for i, line in enumerate(lines) if '-->' in line), None)
        if timing is None:
            continue
        match = re.search(r'((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})', lines[timing])
        if not match:
            continue
        start, end = _stamp(match.group(1)), _stamp(match.group(2))
        text = ' '.join(lines[timing + 1:])
        text = re.sub(r'\s+', ' ', text).strip()
        if start is None or end is None or end <= start or not text:
            continue
        if cues and cues[-1]['text'] == text and start <= cues[-1]['end'] + 1:
            cues[-1]['end'] = max(end, cues[-1]['end'])
        else:
            cues.append({'start': start, 'end': end, 'text': text})
    return cues


def youtube_captions(url, cookies_file=None, directory='captions_cache'):
    out = Path(directory)
    out.mkdir(exist_ok=True)
    cmd = ['yt-dlp', '--no-playlist', '--skip-download', '--write-subs', '--write-auto-subs',
           '--sub-langs', 'ar.*,en.*', '--sub-format', 'vtt', '-o', str(out / '%(id)s.%(ext)s')]
    if cookies_file:
        cmd += ['--cookies', cookies_file]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print('CAPTIONS: ERROR:', type(exc).__name__)
        return []
    files = list(out.glob('*.vtt'))
    # Prefer Arabic; prefer manual if a reliable listing distinguishes it (yt-dlp writes same filename for both).
    files.sort(key=lambda p: (not bool(re.search(r'\.ar(?:-|\.|$)', p.name)), p.name))
    for file in files:
        cues = parse_vtt(file.read_text(encoding='utf-8', errors='replace'))
        if cues:
            print(f'CAPTIONS: YOUTUBE; CUES: {len(cues)}; FILE: {file.name}')
            return cues
    print(f'CAPTIONS: UNAVAILABLE; EXIT_CODE: {result.returncode}; FILES: {len(files)}')
    return []


def audio_from_video(video_path, target='transcription_audio.mp3'):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(video_path),
                    '-vn', '-ac', '1', '-ar', '16000', '-b:a', '48k', target], check=True, timeout=180)
    if not Path(target).is_file() or not Path(target).stat().st_size:
        raise RuntimeError('AUDIO_EXTRACTION_EMPTY')
    return target


def _from_words(words):
    cues = []
    for w in words:
        try:
            start, end = float(w['start']), float(w['end'])
            text = str(w.get('word') or w.get('text') or '').strip()
            if text and end > start >= 0:
                cues.append({'start': start, 'end': end, 'text': text})
        except (KeyError, ValueError, TypeError):
            pass
    return cues


def groq_transcribe(path, word_timestamps=False):
    key = os.getenv('GROQ_API_KEY', '').strip()
    if not key:
        return []
    with open(path, 'rb') as file:
        response = requests.post('https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {key}'},
            data={'model': os.getenv('GROQ_TRANSCRIPTION_MODEL', 'whisper-large-v3'),
                  'response_format': 'verbose_json',
                  'timestamp_granularities[]': 'word' if word_timestamps else 'segment',
                  'language': 'ar'}, files={'file': (Path(path).name, file, 'audio/mpeg')}, timeout=180)
    response.raise_for_status()
    payload = response.json()
    observations = payload.get('words', []) if word_timestamps else payload.get('segments', [])
    return _from_words(observations or payload.get('segments', []))


def deepgram_transcribe(path):
    key = os.getenv('DEEPGRAM_API_KEY', '').strip()
    if not key:
        return []
    with open(path, 'rb') as file:
        response = requests.post('https://api.deepgram.com/v1/listen',
            params={'model': os.getenv('DEEPGRAM_MODEL', 'nova-3'), 'language': 'ar-EG',
                    'punctuate': 'true', 'smart_format': 'true'},
            headers={'Authorization': f'Token {key}', 'Content-Type': 'audio/mpeg'},
            data=file, timeout=180)
    response.raise_for_status()
    alternatives = response.json().get('results', {}).get('channels', [{}])[0].get('alternatives', [])
    return _from_words(alternatives[0].get('words', [])) if alternatives else []


def assemblyai_transcribe(path):
    key = os.getenv('ASSEMBLYAI_API_KEY', '').strip()
    if not key:
        return []
    headers = {'Authorization': key}
    with open(path, 'rb') as file:
        upload = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=file, timeout=180)
    upload.raise_for_status()
    job = requests.post('https://api.assemblyai.com/v2/transcript', headers=headers,
        json={'audio_url': upload.json()['upload_url'], 'speech_models': ['universal-3-pro', 'universal-2'],
              'language_code': 'ar'}, timeout=30)
    job.raise_for_status()
    job_id = job.json()['id']
    import time
    for _ in range(60):
        result = requests.get(f'https://api.assemblyai.com/v2/transcript/{job_id}', headers=headers, timeout=30)
        result.raise_for_status()
        data = result.json()
        if data.get('status') == 'completed':
            return _from_words([{'start': w['start'] / 1000, 'end': w['end'] / 1000, 'text': w['text']}
                                for w in data.get('words', [])])
        if data.get('status') == 'error':
            raise RuntimeError('AssemblyAI transcription failed: ' + str(data.get('error', 'unknown'))[:160])
        time.sleep(5)
    raise TimeoutError('AssemblyAI polling exceeded 300 seconds')


def gemini_transcribe(path):
    """Optional audio transcription. Accept only machine-readable timed segments."""
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        return []
    from google import genai
    client = genai.Client(api_key=key)
    uploaded = client.files.upload(file=path)
    response = client.models.generate_content(
        model=os.getenv('GEMINI_TRANSCRIPTION_MODEL', 'gemini-2.5-flash'),
        contents=[uploaded, 'Transcribe the spoken Arabic and English faithfully. Return JSON only: '
                 '{"segments":[{"start":0.0,"end":1.0,"text":"spoken words"}]}. '
                 'Use seconds relative to the beginning of the audio. Do not invent speech.'],
        config={'response_mime_type': 'application/json'})
    payload = json.loads(response.text)
    return _from_words(payload.get('segments', []))


def save_captions(cues, stem='verified_transcript'):
    """Write raw timed cues as SRT/VTT; content is not claimed to be manually verified."""
    def stamp(seconds, separator):
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        sec, ms = divmod(remainder, 1000)
        return f'{hours:02}:{minutes:02}:{sec:02}{separator}{ms:03}'
    srt = []
    vtt = ['WEBVTT', '']
    for i, cue in enumerate(cues, 1):
        start, end = stamp(cue['start'], ','), stamp(cue['end'], ',')
        srt.extend([str(i), f'{start} --> {end}', cue['text'], ''])
        vtt.extend([f"{stamp(cue['start'], '.')} --> {stamp(cue['end'], '.')}", cue['text'], ''])
    Path(stem + '.srt').write_text('\n'.join(srt), encoding='utf-8')
    Path(stem + '.vtt').write_text('\n'.join(vtt), encoding='utf-8')


def obtain_timed_transcript(url, video_path=None, cookies_file=None,
                            prefer_local_audio=False, prefer_word_timestamps=False):
    """Return (cues, source). Never invent timestamps. Caption first, then provider fallback."""
    if not prefer_local_audio:
        cues = youtube_captions(url, cookies_file)
        if cues:
            return cues, 'youtube_captions'
    if not video_path or not Path(video_path).is_file():
        return [], 'none'
    audio = audio_from_video(video_path)
    providers = {'groq': groq_transcribe, 'deepgram': deepgram_transcribe,
                 'assemblyai': assemblyai_transcribe, 'gemini': gemini_transcribe}
    order = [x.strip().lower() for x in os.getenv('TRANSCRIPTION_PROVIDERS', 'groq,deepgram,assemblyai,gemini').split(',')]
    for name in order:
        fn = providers.get(name)
        if not fn:
            continue
        try:
            cues = fn(audio, word_timestamps=prefer_word_timestamps) if name == 'groq' else fn(audio)
            if cues:
                print(f'TRANSCRIPTION: {name.upper()}; CUES: {len(cues)}')
                return cues, name
            print(f'TRANSCRIPTION: {name.upper()}; EMPTY_OR_NOT_CONFIGURED')
        except Exception as exc:
            print(f'TRANSCRIPTION: {name.upper()}; ERROR: {type(exc).__name__}; {str(exc)[:150]}')
    return [], 'none'


def cues_to_text(cues, limit=20000):
    return '\n'.join(f"[{c['start']:.2f}-{c['end']:.2f}] {c['text']}" for c in cues)[:limit]


def clip_has_coverage(cues, start, end):
    """Validate clip interval overlaps real timed speech, not just plausible integers."""
    if not cues or end <= start:
        return False
    overlap = sum(max(0, min(end, c['end']) - max(start, c['start'])) for c in cues)
    return overlap >= min(10, (end - start) * .20)
