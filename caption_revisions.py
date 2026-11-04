"""Apply only explicitly approved, timestamp-specific transcript corrections.

No LLM paraphrasing: uncertain ASR words are left unchanged until reviewed.
Each replacement is a single word at its ORIGINAL source-video timestamp, keeping
alignment with the audio for word-by-word highlighting.
"""
from __future__ import annotations
import json
import math
from pathlib import Path


def apply_approved_edits(cues, video_url, config='caption_corrections.json'):
    path = Path(config)
    if not path.is_file():
        print('CAPTION_REVIEW: NO_APPROVED_EDITS; ASR_WORDING_NOT_MANUALLY_VERIFIED')
        return cues
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('video_url') != video_url:
        raise ValueError('CAPTION_REVIEW_SOURCE_MISMATCH: refusing corrections for another video')
    changes = payload.get('edits', [])
    if not isinstance(changes, list):
        raise ValueError('CAPTION_REVIEW_FORMAT_INVALID')
    result = [dict(c) for c in cues]
    used = set()
    for edit in changes:
        try:
            position = float(edit['start'])
            original = str(edit['from']).strip()
            corrected = str(edit['to']).strip()
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError('CAPTION_REVIEW_EDIT_INVALID') from exc
        if not math.isfinite(position) or position < 0 or not original or not corrected or \
                any(ch.isspace() for ch in original + corrected) or \
                any(ch in '\n\r{}\\' for ch in original + corrected):
            raise ValueError('CAPTION_REVIEW_EDIT_INVALID: only one-word corrections supported')
        options = [(idx, abs(float(c['start'])-position)) for idx, c in enumerate(result)
                   if c.get('text') == original and abs(float(c['start'])-position) <= .15]
        options.sort(key=lambda item: item[1])
        if not options or len(options) > 1 and abs(options[0][1]-options[1][1]) < .01:
            raise ValueError(f'CAPTION_REVIEW_EDIT_NOT_FOUND: {original} near {position:.2f}s')
        idx = options[0][0]
        if idx in used:
            raise ValueError('CAPTION_REVIEW_DUPLICATE_EDIT')
        result[idx]['text'] = corrected
        used.add(idx)
    print(f'CAPTION_REVIEW: APPROVED_EDITS_APPLIED; COUNT: {len(used)}')
    return result
