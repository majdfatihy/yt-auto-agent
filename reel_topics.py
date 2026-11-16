"""Transcription-first semantic clip planning.

Only AI-grounded topic proposals are listed; never silently fall back to
anonymous fixed-duration intervals. Previously uploaded spans are excluded.
"""
from datetime import datetime, timezone
import math
import re

from clip_planner import _valid_cues, _speech_overlap
from reel_history import published_windows, overlaps
from reel_full_plan import remaining_intervals, MAX_SOURCE_REELS
from reel_metadata import clip_transcript, numbered_seo_title
from reel_title_quality import valid_title
from text_ai import generate_json

MIN_REEL_SECONDS = 15
ALLOWED_LIMITS = {30, 59, 180}


def _segments(start, end, cues, max_seconds):
    """Model context windows anchored to real cue endings, no synthetic cuts."""
    cursor = start
    windows = []
    # A short window lets the model see neighboring ideas in full.
    target = max(100, min(400, max_seconds * 2 + 90))
    while cursor + MIN_REEL_SECONDS <= end:
        desired = min(end, cursor + target)
        nearby = [c["end"] for c in cues if cursor + MIN_REEL_SECONDS <= c["end"] <= end
                  and abs(c["end"] - desired) <= 20]
        right = min(nearby, key=lambda t: abs(t - desired)) if nearby else desired
        if end - right < MIN_REEL_SECONDS:
            right = end
        if right <= cursor:
            break
        windows.append((round(cursor, 2), round(right, 2)))
        cursor = right
    return windows


def _topic_candidates(cues, left, right, max_seconds, api_key):
    within = [c for c in cues if left <= c["start"] < right]
    if not within or _speech_overlap(within, left, right) < 7:
        return []
    timed_text = "\n".join(
        f"[{c['start']:.1f}-{c['end']:.1f}] {c['text']}" for c in within)[:18500]
    prompt = (
        "اقرأ تفريغ الكلام المؤقت بالكامل قبل الإجابة. قسّمه إلى موضوعات "
        "مستقلة مفهومة وقابلة للمشاهدة وحدها، بالترتيب الزمني. "
        "لا تقطع في وسط الجملة أو الشرح، ولا تستخدم تقطيعًا بمدة ثابتة. "
        "لكل موضوع قدّم عنوان بحث عربي محدد وصادق، هاشتاغ موضوعي واحد "
        "مرتبط بكلمات النص، وتوقيت بداية ونهاية حقيقيين من التفريغ. "
        "تجنب التحية والحشو والإعلانات والمقدمات والسكوت. لا تضف فكرة غير منطوقة. "
        f"الحد الأدنى {MIN_REEL_SECONDS} ثانية والحد الأقصى {max_seconds} ثانية. "
        f"النطاق {left:.1f} إلى {right:.1f} ثانية. "
        "أي مقطع لا يكمل فكرة اتركه بدون اقتراح. "
        "اكتب JSON فقط: "
        '{"proposals":[{"title":"عنوان بحثي","hashtag":"#كلمة_محددة",'
        '"start_time":12.3,"end_time":52.1}]}.\n\n' + timed_text
    )

    def checked(data):
        proposals = data.get("proposals") if isinstance(data, dict) else None
        if not isinstance(proposals, list):
            raise ValueError("TOPIC_AI_INVALID_OUTPUT")
        valid = []
        for item in proposals[:12]:
            if not isinstance(item, dict):
                continue
            try:
                a, b = float(item["start_time"]), float(item["end_time"])
                title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
                tag = str(item.get("hashtag") or "").strip()
            except (KeyError, ValueError, TypeError):
                continue
            if not (math.isfinite(a) and math.isfinite(b) and
                    left - .1 <= a < b <= right + .1 and
                    MIN_REEL_SECONDS <= b-a <= max_seconds and
                    re.fullmatch(r"#[\w\u0600-\u06FF]{2,35}", tag) and
                    _speech_overlap(within, a, b) >= min(7.0, .2*(b-a))):
                continue
            words = " ".join(c["text"] for c in within if c["start"] < b and c["end"] > a)
            if not valid_title(title, words):
                continue
            valid.append(dict(title=title, hashtag=tag,
                              start_time=round(a, 2), end_time=round(b, 2)))
        if not valid:
            raise ValueError("TOPIC_AI_NO_COMPLETE_IDEAS")
        return valid
    try:
        candidates, provider = generate_json(prompt, api_key, "CLIP_PLANNER_MODELS", checked)
        return [{**x, "model": provider} for x in candidates]
    except RuntimeError as err:
        print(f"TOPIC_WINDOW_SKIPPED: {left:.1f}-{right:.1f}; {type(err).__name__}")
        return []


def build_topic_plan(video_url, telegram_file_id, timed_cues, duration, history,
                     api_key, max_seconds=59):
    if max_seconds not in ALLOWED_LIMITS:
        raise ValueError("TOPIC_REEL_LIMIT_INVALID")
    cues = _valid_cues(timed_cues)
    if not cues or duration < MIN_REEL_SECONDS:
        raise ValueError("TOPIC_TRANSCRIPT_TOO_SHORT")
    published = published_windows(history)
    serials = [x["serial"] for x in history if isinstance(x, dict) and
               type(x.get("serial")) is int and x["serial"] >= 1]
    serial_floor = max([len(published), *serials])
    candidates = []
    for first, last in remaining_intervals(duration, published):
        for a, b in _segments(first, last, cues, max_seconds):
            for topic in _topic_candidates(cues, a, b, max_seconds, api_key):
                x, y = topic["start_time"], topic["end_time"]
                if any(overlaps(x, y, i, j) for i, j in published):
                    continue
                if any(overlaps(x, y, other["start_time"], other["end_time"])
                       for other in candidates):
                    continue
                candidates.append(topic)
                if len(candidates) >= MAX_SOURCE_REELS:
                    break
            if len(candidates) >= MAX_SOURCE_REELS:
                break
        if len(candidates) >= MAX_SOURCE_REELS:
            break
    if not candidates:
        raise RuntimeError("NO_GROUNDED_TOPICS: لم يستطع الذكاء الاصطناعي اقتراح موضوعات مكتملة؛ لن يتم قص عشوائي.")
    candidates.sort(key=lambda item: item["start_time"])
    clips, transcripts = [], {}
    for i, topic in enumerate(candidates, 1):
        start, end = topic["start_time"], topic["end_time"]
        transcript = clip_transcript(cues, start, end)
        serial = serial_floor + i
        title = numbered_seo_title(topic["title"], [topic["hashtag"]], serial, transcript)
        clips.append(dict(id=i, serial=serial, title=title, reel_title=title,
                          title_approved=False, start_time=start, end_time=end,
                          transcript_preview=transcript[:180],
                          source_window_key=f"{start:.2f}:{end:.2f}",
                          reel_max_seconds=max_seconds))
        transcripts[str(i)] = transcript
    return ({
        "version": 3, "status": "awaiting_selection", "method": "topic_transcript_ai",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "video_url": video_url, "telegram_file_id": telegram_file_id,
        "duration": round(duration, 2), "clips": clips,
        "recommended_clip_id": clips[0]["id"],
        "previously_uploaded": len(published),
        "default_privacy": "public",
        "clip_jobs": {},
    }, transcripts)
