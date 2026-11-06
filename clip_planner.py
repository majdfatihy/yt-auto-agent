"""Stage 1: transcript-grounded clip suggestions, with NO video cutting or publishing.

The suggestions are persisted to a GitHub Actions repository variable so a future
explicit selection command can use the same numbered options.
"""
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import requests

MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 59
MAX_PROPOSALS = 5
PLAN_VARIABLE = "TELEGRAM_CLIP_PROPOSALS"


def _valid_cues(cues):
    result = []
    for cue in cues:
        try:
            start, end = float(cue["start"]), float(cue["end"])
            text = re.sub(r"\s+", " ", str(cue["text"])).strip()
            if math.isfinite(start) and math.isfinite(end) and 0 <= start < end and text:
                result.append({"start": start, "end": end, "text": text})
        except (TypeError, ValueError, KeyError):
            continue
    return sorted(result, key=lambda item: (item["start"], item["end"]))


def source_duration(video_file, cues):
    """Trust ffprobe if a validated local file exists; else bound to last timed cue."""
    if video_file and Path(video_file).is_file():
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_file)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
        return duration
    return max((cue["end"] for cue in cues), default=0.0)


def _speech_overlap(cues, start, end):
    """Calculate union duration of speech intervals (avoid double counting)."""
    ranges = sorted((max(start, cue["start"]), min(end, cue["end"])) for cue in cues
                    if cue["end"] > start and cue["start"] < end)
    total, cursor = 0.0, start
    for left, right in ranges:
        if right > max(left, cursor):
            total += right - max(left, cursor)
            cursor = right
    return total


def _covered(cues, start, end):
    return _speech_overlap(cues, start, end) >= min(7.0, (end - start) * 0.16)


def _near_speech_boundary(cues, second, kind):
    # The chosen edge may lie inside a speech segment; don't require exact timestamps.
    return any(cue["start"] - 3 <= second <= cue["end"] + 3 for cue in cues)


def _validate(proposal, cues, duration):
    if not isinstance(proposal, dict):
        return None
    title = re.sub(r"\s+", " ", str(proposal.get("title") or "")).strip()
    if not title or len(title) > 95:
        return None
    try:
        start, end = float(proposal["start_time"]), float(proposal["end_time"])
    except (TypeError, ValueError, KeyError):
        return None
    if not all(map(math.isfinite, (start, end))):
        return None
    start, end = round(start, 1), round(end, 1)
    if (start < 0 or end > duration + 0.1 or
            end - start < MIN_CLIP_SECONDS or end - start > MAX_CLIP_SECONDS or
            not _covered(cues, start, end) or
            not _near_speech_boundary(cues, start, "start") or
            not _near_speech_boundary(cues, end, "end")):
        return None
    return {"title": title, "start_time": start, "end_time": end}


def _fallback_title(cues, start, end):
    pieces = [cue["text"] for cue in cues if cue["start"] < end and cue["end"] > start]
    words = re.sub(r"\s+", " ", " ".join(pieces)).strip().split()
    # Quote a real transcript excerpt rather than inventing a topic or promise.
    extract = " ".join(words[:9]).strip(" ،.:-")
    return ("مقتطف: " + extract)[:90] if extract else "مقطع من التفريغ الصوتي"


def _fallback(cues, duration, target_count):
    """Provide bounded, non-invented timing options if Gemini is unavailable."""
    if duration < MIN_CLIP_SECONDS:
        return []
    target_count = max(1, min(MAX_PROPOSALS, target_count))
    candidates = []
    # Distribute candidate positions across available speech, not blank footage.
    last_start = max(0.0, duration - min(38.0, duration))
    for index in range(target_count * 3):
        fraction = index / max(1, target_count * 3 - 1)
        desired = last_start * fraction
        near = min(cues, key=lambda c: abs(c["start"] - desired))
        start = max(0.0, min(near["start"], duration - MIN_CLIP_SECONDS))
        ideal_end = min(duration, start + 38.0)
        endings = [c["end"] for c in cues if start + MIN_CLIP_SECONDS <= c["end"] <= min(duration, start + MAX_CLIP_SECONDS)]
        end = min(endings, key=lambda item: abs(item - ideal_end)) if endings else ideal_end
        proposal = _validate({"title": _fallback_title(cues, start, end),
                              "start_time": start, "end_time": end}, cues, duration)
        if proposal and not any(abs(proposal["start_time"] - old["start_time"]) < 9 for old in candidates):
            candidates.append(proposal)
        if len(candidates) >= target_count:
            break
    return candidates


def _gemini_candidates(cues, duration, key):
    if not key:
        print("CLIP_PLANNER: GEMINI_API_KEY unavailable; grounded fallback")
        return []
    try:
        from google import genai
        client = genai.Client(api_key=key)
    except Exception as error:
        print(f"CLIP_PLANNER: SDK_ERROR: {type(error).__name__}")
        return []
    timed_text = "\n".join(f"[{cue['start']:.1f}-{cue['end']:.1f}] {cue['text']}" for cue in cues)[:19000]
    prompt = (
        "أنت محلل فيديو عربي. اقترح من 1 إلى 5 مقاطع مختلفة صالحة للشورتس، "
        "بناءً فقط على الكلام المؤقت أدناه. لا تُنشئ فيديو ولا بيانات سيو ولا وصفًا ولا هاشتاجات. "
        "العنوان لكل مقطع عربي قصير، موضوعي، يصف الكلام الموجود فعلًا من غير وعود أو ادعاءات. "
        f"مدة كل اقتراح من {MIN_CLIP_SECONDS} إلى {MAX_CLIP_SECONDS} ثانية، "
        f"وتوقيتاته بالثواني داخل المصدر من 0 إلى {duration:.1f}. "
        "اربط البداية والنهاية بحدود الكلام المنطوق وتجنب القطع وسط الجمل. "
        "قلل التداخل بين المقاطع قدر الإمكان، ولا تكرر الفكرة نفسها. "
        "أرجع JSON فقط بالشكل: "
        '{"proposals":[{"title":"عنوان من الكلام الفعلي","start_time":0,"end_time":30}]}. '
        "إن لم يوجد مقطع مناسب أرجع مصفوفة فارغة.\n\n"
        + timed_text
    )
    models = [s.strip() for s in os.getenv("CLIP_PLANNER_MODELS", "gemini-3-flash-preview,gemini-2.5-flash").split(",") if s.strip()]
    for model_name in models:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt,
                                                       config={"response_mime_type": "application/json"})
            raw = json.loads(response.text)
            options = raw.get("proposals") if isinstance(raw, dict) else None
            if isinstance(options, list):
                print(f"CLIP_PLANNER: GEMINI_MODEL: {model_name}; CANDIDATES: {len(options)}")
                return options[:12]
            print(f"CLIP_PLANNER: BAD_RESPONSE: {model_name}")
        except Exception as error:
            print(f"CLIP_PLANNER: GEMINI_ERROR: {model_name}: {type(error).__name__}: {str(error)[:130]}")
    return []


def build_clip_plan(video_url, telegram_file_id, timed_cues, duration, gemini_key):
    cues = _valid_cues(timed_cues)
    if not cues or duration < MIN_CLIP_SECONDS:
        raise ValueError("لا توجد مقاطع كلام مؤقتة كافية لاقتراح قص صالح")
    desired = min(MAX_PROPOSALS, max(1, round(duration / 30)))
    suggestions = []
    gemini_options = _gemini_candidates(cues, duration, gemini_key)
    for item in gemini_options:
        candidate = _validate(item, cues, duration)
        if candidate and not any(abs(candidate["start_time"] - old["start_time"]) < 8 and
                                 abs(candidate["end_time"] - old["end_time"]) < 8 for old in suggestions):
            suggestions.append(candidate)
        if len(suggestions) == desired:
            break
    ai_count = len(suggestions)
    if len(suggestions) < desired:
        for candidate in _fallback(cues, duration, desired):
            if not any(abs(candidate["start_time"] - old["start_time"]) < 9 for old in suggestions):
                suggestions.append(candidate)
            if len(suggestions) == desired:
                break
    if not suggestions:
        raise ValueError("لم يمكن اقتراح توقيتات معتمدة على الكلام الفعلي")
    suggestions = sorted(suggestions, key=lambda x: x["start_time"])
    clips = [{"id": index, **proposal} for index, proposal in enumerate(suggestions, 1)]
    plan = {"version": 1, "status": "awaiting_selection", "video_url": video_url,
            "telegram_file_id": telegram_file_id, "duration": round(duration, 1),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "method": "gemini" if ai_count == len(clips) else ("mixed" if ai_count else "timed_transcript_fallback"),
            "clips": clips}
    return plan


def save_clip_plan(plan):
    """Persist latest plan using the PAT already used by telegram_poll.py."""
    token = os.getenv("GH_PAT", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        raise RuntimeError("CLIP_PLAN_STORAGE_NOT_CONFIGURED: GH_PAT/GITHUB_REPOSITORY missing")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    root = f"https://api.github.com/repos/{repo}/actions/variables"
    value = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    response = requests.get(f"{root}/{PLAN_VARIABLE}", headers=headers, timeout=20)
    if response.status_code == 404:
        result = requests.post(root, headers=headers,
                               json={"name": PLAN_VARIABLE, "value": value}, timeout=20)
        expected = 201
    else:
        response.raise_for_status()
        result = requests.patch(f"{root}/{PLAN_VARIABLE}", headers=headers,
                                json={"value": value}, timeout=20)
        expected = 204
    if result.status_code != expected:
        raise RuntimeError(f"CLIP_PLAN_STORAGE_FAILED: GitHub HTTP {result.status_code}; check GH_PAT permissions")
    print(f"CLIP_PLAN: SAVED; CLIPS: {len(plan['clips'])}; STORAGE: {PLAN_VARIABLE}")


def _stamp(seconds):
    whole = round(seconds)
    minutes, sec = divmod(whole, 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours:02}:{mins:02}:{sec:02}" if hours else f"{mins:02}:{sec:02}"


def format_clip_plan(plan):
    lines = ["اقتراحات القص — لم يتم قص أي فيديو:", ""]
    for clip in plan["clips"]:
        lines.append(f"{clip['id']}. {clip['title']}")
        lines.append(f"   {_stamp(clip['start_time'])} → {_stamp(clip['end_time'])} | {round(clip['end_time'] - clip['start_time'])} ثانية")
        lines.append("")
    if plan["method"] != "gemini":
        lines.append("ملاحظة: بعض العناوين مقتطفات حرفية من التفريغ لأن التحليل الذكي لم يوفر اقتراحات كافية.")
    lines.append("للمعاينة: أرسل رقمًا مثل 1 أو عدة أرقام مثل 1,3 أو /clip 1,3.")
    lines.append("لم يُقص أو يُنشر أي فيديو.")
    return "\n".join(lines)


# Stage 2: explicit, validated selections; no publishing in this module.
def plan_reference(plan):
    """Unique UTC plan identity, shared by the Cloudflare webhook and GitHub runner.

    The selected source and Telegram file ID are checked separately.
    """
    reference = plan.get('created_utc') if isinstance(plan, dict) else None
    if not isinstance(reference, str) or len(reference) < 20 or len(reference) > 50:
        raise ValueError('CLIP_PLAN_REF_INVALID')
    return reference


def parse_clip_choices(value, plan):
    """Allow `1`, `1,3`, `١،٣`, or `1.2.3`; reject arbitrary text and unknown IDs."""
    if plan.get('status') not in ('awaiting_selection', 'previewed_awaiting_approval'):
        raise ValueError('قائمة الاقتراحات ليست متاحة للاختيار. أرسل /prepare أولًا.')
    text = str(value).strip().translate(str.maketrans(
        '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'))
    if not re.fullmatch(r'[0-9]+(?:[\s,،.]+[0-9]+)*', text):
        raise ValueError('صيغة الاختيار غير صحيحة. استخدم 1 أو 1,3 أو /clip 1,3.')
    raw_ids = [int(token) for token in re.split(r'[\s,،.]+', text)]
    entries = plan.get('clips', [])
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PROPOSALS:
        raise ValueError('قائمة الاقتراحات غير صالحة')
    try:
        source_seconds = float(plan['duration'])
    except (ValueError, TypeError, KeyError):
        raise ValueError('مدة المصدر غير صالحة') from None
    if not math.isfinite(source_seconds) or source_seconds < MIN_CLIP_SECONDS:
        raise ValueError('مدة المصدر غير صالحة')
    valid_ids = set()
    for clip in entries:
        if not isinstance(clip, dict) or type(clip.get('id')) is not int or clip['id'] in valid_ids:
            raise ValueError('ترقيم اقتراحات القص غير صالح')
        if not isinstance(clip.get('title'), str) or not clip['title'].strip() or len(clip['title']) > 95:
            raise ValueError('عنوان مقطع غير صالح')
        try:
            start, end = float(clip['start_time']), float(clip['end_time'])
        except (TypeError, ValueError, KeyError):
            raise ValueError('توقيت مقطع غير صالح') from None
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0
                or end > source_seconds + 0.11 or not MIN_CLIP_SECONDS <= end - start <= MAX_CLIP_SECONDS + 0.01):
            raise ValueError('توقيت مقطع خارج حدود الفيديو')
        valid_ids.add(clip['id'])
    if (not valid_ids or len(raw_ids) > MAX_PROPOSALS or len(set(raw_ids)) != len(raw_ids)
            or any(i not in valid_ids for i in raw_ids)):
        raise ValueError('رقم مقطع غير موجود في الاقتراحات المحفوظة. استخدم /prepare لعرض قائمة جديدة.')
    return raw_ids


def load_clip_plan():
    token = os.getenv('GH_PAT', '').strip()
    repo = os.getenv('GITHUB_REPOSITORY', '').strip()
    if not token or not repo:
        raise RuntimeError('CLIP_PLAN_STORAGE_NOT_CONFIGURED: GH_PAT/GITHUB_REPOSITORY missing')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    response = requests.get(f'https://api.github.com/repos/{repo}/actions/variables/{PLAN_VARIABLE}',
                            headers=headers, timeout=20)
    if response.status_code == 404:
        raise ValueError('لا توجد اقتراحات محفوظة. استخدم /prepare أولًا.')
    response.raise_for_status()
    try:
        plan = json.loads(response.json()['value'])
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError('CLIP_PLAN_INVALID_JSON') from error
    if not isinstance(plan, dict) or not isinstance(plan.get('clips'), list):
        raise ValueError('قائمة الاقتراحات غير صالحة. أعد /prepare.')
    return plan


def record_preview_result(expected_reference, clip_ids, run_id=None):
    """Avoid overwriting a newer /prepare list after rendering an old selection."""
    latest = load_clip_plan()
    if plan_reference(latest) != expected_reference:
        raise RuntimeError('CLIP_PLAN_CHANGED: new suggestions were saved during rendering')
    latest['status'] = 'previewed_awaiting_approval'
    latest['previewed_ids'] = clip_ids
    latest['previewed_utc'] = datetime.now(timezone.utc).isoformat()
    latest['preview_run_id'] = str(run_id or os.getenv('GITHUB_RUN_ID', '')).strip()
    if not latest['preview_run_id'].isdigit():
        raise RuntimeError('PREVIEW_RUN_ID_MISSING: refuse approval without an artifact run')
    save_clip_plan(latest)
