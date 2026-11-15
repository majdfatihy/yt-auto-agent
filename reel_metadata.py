"""Create transcript-grounded title, three-line description and topical hashtags per approved reel.

This module never uploads anything and never adds source links to video descriptions.
"""
import json
import os
import re
from collections import Counter

STOP_WORDS = {
    'اللي', 'الذي', 'التي', 'ان', 'إن', 'على', 'علي', 'وفي', 'الى', 'إلى', 'هذا',
    'هذه', 'هنا', 'هناك', 'يعني', 'تمام', 'عشان', 'عند', 'عندي', 'معانا', 'ممكن',
    'علشان', 'احنا', 'هنعمل', 'عليه', 'عليها', 'كده', 'طبعا', 'دلوقتي',
    'عندما', 'لكن', 'وبعد', 'هتلاقي', 'فيه', 'فيها', 'ومن', 'تقدر', 'النهارده'
}
URL = re.compile(r'(?:https?://|www\.|youtu\.be/|youtube\.com/)', re.I)


def clip_transcript(cues, start, end):
    """Use only speech that actually overlaps this reel, not whole-video copy."""
    pieces = []
    for cue in cues:
        try:
            left, right = float(cue['start']), float(cue['end'])
            phrase = re.sub(r'\s+', ' ', str(cue.get('text', '')).strip())
        except (KeyError, TypeError, ValueError):
            continue
        if left < end and right > start and phrase and (not pieces or pieces[-1] != phrase):
            pieces.append(phrase)
    transcript = ' '.join(pieces).strip()
    if len(transcript.split()) < 8:
        raise RuntimeError('REEL_METADATA_INSUFFICIENT_CLIP_TRANSCRIPT')
    return transcript


def _validate(value):
    if not isinstance(value, dict):
        raise ValueError('REEL_METADATA_NOT_OBJECT')
    title = re.sub(r'\s+', ' ', str(value.get('title') or '')).strip()
    lines = value.get('description_lines')
    hashtags = value.get('hashtags')
    if not 8 <= len(title) <= 100 or URL.search(title):
        raise ValueError('REEL_METADATA_INVALID_TITLE')
    if not isinstance(lines, list) or len(lines) != 3:
        raise ValueError('REEL_METADATA_INVALID_DESCRIPTION_LINES')
    if any(not isinstance(line, str) or not 8 <= len(line.strip()) <= 200 or URL.search(line)
           for line in lines):
        raise ValueError('REEL_METADATA_INVALID_DESCRIPTION_TEXT')
    if not isinstance(hashtags, list) or not 3 <= len(hashtags) <= 6:
        raise ValueError('REEL_METADATA_INVALID_HASHTAGS')
    if any(not isinstance(tag, str) or not re.fullmatch(r'#[\w]+', tag, re.UNICODE)
           or len(tag) > 42 for tag in hashtags):
        raise ValueError('REEL_METADATA_INVALID_HASHTAG_FORMAT')
    if len(set(hashtags)) != len(hashtags):
        raise ValueError('REEL_METADATA_REPEATED_HASHTAGS')
    result = {'title': title, 'description': '\n'.join(line.strip() for line in lines)
              + '\n' + ' '.join(hashtags)}
    return result


def _fallback(transcript, suggested_title):
    """Never invent facts when the AI is unavailable: use literal spoken words."""
    words = transcript.split()
    title = re.sub(r'\s+', ' ', str(suggested_title or '')).strip()
    title = URL.sub('', title)
    if not 8 <= len(title) <= 100:
        title = ' '.join(words[:12])[:100].strip()
    # Three readable, extractive lines drawn directly from this clip's own speech.
    output = []
    remaining = words[:]
    for index in range(3):
        if not remaining:
            output.append(' '.join(words[max(0, len(words) - 9):]))
            continue
        count = max(1, min(len(remaining) - max(0, 2 - index),
                           max(6, min(22, (len(remaining) + 2 - index) // (3 - index)))))
        count = max(1, count)
        output.append(' '.join(remaining[:count])[:200].strip())
        remaining = remaining[count:]
    # Specific hashtags come from words actually present in this clip.
    candidates = [word.strip('،.!؟:;()-_#') for word in words]
    candidates = [word for word in candidates if len(word) >= 4 and
                  re.fullmatch(r'[\w]+', word, re.UNICODE) and word.lower() not in STOP_WORDS]
    ranked = [word for word, _ in Counter(candidates).most_common(20)]
    tags = ['#شورتس', '#شرح']
    for word in ranked:
        tag = '#' + word
        if tag not in tags and len(tag) <= 42:
            tags.append(tag)
        if len(tags) == 5:
            break
    while len(tags) < 3:
        tags.append('#معلومات' if '#معلومات' not in tags else '#فيديو')
    return _validate({'title': title, 'description_lines': output, 'hashtags': tags})


def generate_reel_metadata(cues, start, end, suggested_title, api_key=None):
    """Generate a distinct, source-link-free metadata block for each selected reel."""
    transcript = clip_transcript(cues, start, end)
    key = (api_key or '').strip()
    if key:
        from google import genai
        client = genai.Client(api_key=key)
        prompt = (
            'اكتب بيانات وصفية عربية لفيديو قصير بناءً حصريًا على الكلام المفرّغ من '
            'هذا المقطع المحدد، لا على الفيديو الكامل. لا تخترع ميزات أو نتائج أو أرقام '
            'لم ينطق بها المتحدث. لا تضع رابطًا أو طلبًا لمشاهدة الفيديو الأصلي. '
            'أعد JSON صالحًا فقط بالمفاتيح: title نص عنوان عربي واضح ومحدد أقل من 100 حرف؛ '
            'description_lines مصفوفة من ثلاث جمل قصيرة مفيدة للبحث تلخص ما قيل فعلًا؛ '
            'hashtags مصفوفة من 3 إلى 6 هاشتاغات قصيرة، بينها هاشتاغات متخصصة متصلة '
            'بموضوع المقطع حرفيًا وأخرى عامة، بدون مسافات داخل الهاشتاغ. '
            'تجنب التعبيرات المبالغ فيها أو الكلمات العامة غير المتعلقة بالمقطع. '
            'العنوان الاقتراحي (ليس مصدرًا للمعلومات): ' + str(suggested_title or '') +
            '\nالتفريغ النصي الفعلي للمقطع:\n' + transcript[:6500]
        )
        models = [x.strip() for x in os.getenv(
            'REEL_METADATA_MODELS', 'gemini-3-flash-preview,gemini-2.5-flash'
        ).split(',') if x.strip()]
        for model in models:
            try:
                answer = client.models.generate_content(
                    model=model, contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                result = _validate(json.loads(answer.text))
                result['method'] = 'gemini:' + model
                return result
            except Exception as error:
                print('REEL_METADATA_AI_RETRY:', model, type(error).__name__)
    result = _fallback(transcript, suggested_title)
    result['method'] = 'extractive_fallback'
    return result
