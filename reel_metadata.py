"""Evidence-based, per-clip YouTube Shorts SEO derived from full-video and clip transcripts.

The full transcript supplies context; the selected segment limits the claims.
Never fabricate recording/publication dates, source links, or video content.
"""
import re
from text_ai import generate_json
from reel_title_quality import valid_title, _norm

URL = re.compile(r"(?:https?://|www\.|youtu\.be/|youtube\.com/)", re.I)
HASH = re.compile(r"#[\w]+$", re.UNICODE)


def clip_transcript(cues, start, end):
    phrases = []
    for cue in cues:
        try:
            left, right = float(cue["start"]), float(cue["end"])
            words = re.sub(r"\s+", " ", str(cue.get("text", "")).strip())
        except (KeyError, ValueError, TypeError):
            continue
        if left < end and right > start and words and (not phrases or words != phrases[-1]):
            phrases.append(words)
    result = " ".join(phrases).strip()
    if len(result.split()) < 8:
        raise RuntimeError("REEL_METADATA_INSUFFICIENT_CLIP_TRANSCRIPT")
    return result


def full_video_context(cues, max_chars=32000):
    """Read every timed segment; sample uniformly only if exceeding model budget."""
    items = []
    for cue in cues:
        try:
            stamp = float(cue["start"])
            phrase = re.sub(r"\s+", " ", str(cue.get("text", "")).strip())
            if phrase:
                items.append(f"[{stamp:.1f}s] {phrase}")
        except (ValueError, TypeError, KeyError):
            continue
    if not items:
        raise RuntimeError("REEL_FULL_TRANSCRIPT_UNAVAILABLE")
    joined = "\n".join(items)
    if len(joined) <= max_chars:
        return joined
    # Even coverage from beginning to end, not merely truncation of the opening.
    step = max(1, -(-len(joined) // max_chars))
    sampled = [line for i, line in enumerate(items) if i % step == 0]
    result = "\n".join(sampled)
    if len(result) > max_chars:
        result = result[:max_chars]
    return "نماذج موزعة على كامل الفيديو الطويل (وليس تفريغًا كاملًا):\n" + result


def youtube_tag_cost(tags):
    """YouTube counts commas and two quote characters around multiword tags."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def parse_youtube_tags(value):
    if not isinstance(value, str):
        raise ValueError("REEL_TAGS_NOT_STRING")
    if len(value) > 500 or "\n" in value or URL.search(value):
        raise ValueError("REEL_TAGS_TOO_LONG_OR_INVALID")
    tags = [t.strip() for t in value.split(",")]
    if not 6 <= len(tags) <= 20 or any(not t or len(t) > 65 or "#" in t for t in tags):
        raise ValueError("REEL_TAGS_INVALID_ITEMS")
    if len({t.casefold() for t in tags}) != len(tags):
        raise ValueError("REEL_TAGS_DUPLICATED")
    if youtube_tag_cost(tags) > 500:
        raise ValueError("REEL_TAGS_EXCEED_YOUTUBE_500_CHARS")
    return tags


def spoken_language_from_transcript(text):
    """Informational inference only, never a recording date or official API metadata."""
    arabic = len(re.findall(r"[\u0600-\u06ff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if not arabic and not latin:
        return None
    if arabic / (arabic + latin) >= 0.7:
        return "ar"
    if latin / (arabic + latin) >= 0.85:
        return "en"
    return None


def _validate(payload, clip_text=None, full_context=None):
    if not isinstance(payload, dict):
        raise ValueError("REEL_METADATA_NOT_OBJECT")
    title = " ".join(str(payload.get("title") or "").split()).strip()
    if not valid_title(title, clip_text) or URL.search(title):
        raise ValueError("REEL_METADATA_INVALID_OR_UNGROUNDED_TITLE")
    lines = payload.get("description_lines")
    if not isinstance(lines, list) or len(lines) != 3:
        raise ValueError("REEL_METADATA_DESCRIPTION_MUST_HAVE_THREE_LINES")
    if any(not isinstance(x, str) or not 18 <= len(x.strip()) <= 190
           or URL.search(x) or "\n" in x for x in lines):
        raise ValueError("REEL_METADATA_DESCRIPTION_INVALID")
    tags = payload.get("hashtags")
    if not isinstance(tags, list) or not 3 <= len(tags) <= 6 or any(
            not isinstance(x, str) or not HASH.fullmatch(x) or len(x) > 45
            for x in tags):
        raise ValueError("REEL_METADATA_HASHTAGS_INVALID")
    if len(set(tags)) != len(tags) or all(x in ("#شورتس", "#شرح", "#فيديو") for x in tags):
        raise ValueError("REEL_METADATA_HASHTAGS_NOT_SPECIFIC")
    keywords = payload.get("keywords")
    if not isinstance(keywords, list):
        raise ValueError("REEL_TAGS_NOT_LIST")
    terms = [x.strip() for x in keywords if isinstance(x, str)]
    if len(terms) != len(keywords):
        raise ValueError("REEL_TAGS_NON_TEXT_ENTRY")
    tags_string = ",".join(terms)
    parse_youtube_tags(tags_string)

    # The hashtags and tags must include topic words actually spoken in the clip
    # or the wider video. Reject generic, disconnected sets.
    known_words = {_norm(x) for x in ((clip_text or "") + " " + (full_context or "")).split()}
    known_words.discard("")
    if known_words:
        topical_count = sum(any(_norm(part) in known_words and len(_norm(part)) >= 4
                                for part in tag.replace("_", " ").split())
                            for tag in tags if tag not in ("#شورتس", "#شرح", "#فيديو"))
        if topical_count == 0:
            raise ValueError("REEL_METADATA_HASHTAGS_NOT_IN_TRANSCRIPT")
        keyword_topics = sum(any(_norm(part) in known_words and len(_norm(part)) >= 4
                                 for part in phrase.split()) for phrase in terms)
        if keyword_topics < 3:
            raise ValueError("REEL_METADATA_KEYWORDS_NOT_IN_TRANSCRIPT")

    description = "\n".join(line.strip() for line in lines) + "\n" + " ".join(tags)
    if len(description.encode("utf-8")) > 5000:
        raise ValueError("REEL_METADATA_DESCRIPTION_EXCEEDS_5000_BYTES")
    return {
        "title": title,
        "hashtags": tags,
        "description": description,
        "tags_string": tags_string,
        "language": "ar",
    }


def numbered_seo_title(base_title, hashtags, number, clip_text):
    """One publish-ready SEO title: topical hashtag then its sequence number."""
    if type(number) is not int or not 1 <= number <= 9999:
        raise ValueError("REEL_INVALID_SEQUENTIAL_NUMBER")
    candidates = [t for t in hashtags if t not in ("#شورتس", "#شرح", "#فيديو")]
    hashtag = candidates[0] if candidates else ""
    suffix = ((" " + hashtag) if hashtag else "") + " " + str(number)
    words = base_title.split()
    while words and len(" ".join(words)) + len(suffix) > 100:
        words.pop()
    core = " ".join(words)
    if not valid_title(core, clip_text):
        raise ValueError("REEL_SEO_NUMBERED_TITLE_INVALID")
    title = core + suffix
    if not title.endswith(" " + str(number)) or len(title) > 100:
        raise ValueError("REEL_SEO_NUMBERED_TITLE_OVER_100")
    return title


def generate_reel_metadata(cues, start, end, suggested_title, api_key=None, clip_number=None):
    clip_text = clip_transcript(cues, start, end)
    whole_text = full_video_context(cues)
    prompt = (
        "أنت محرر SEO لقناة عربية. اقرأ سياق تفريغ الفيديو بالكامل أولًا لفهم "
        "المنصة والموضوع والجمهور والمصطلحات، ثم ركّز حصريًا على محتوى المقطع "
        f"من الثانية {start} إلى {end}. لا تختلق أي تفاصيل من سياق الفيديو "
        "الكامل لم تظهر أو تُشرح في المقطع المحدد. "
        "اكتب عنوان نشر واحدًا فقط، واضحًا للبحث، مرتبطًا بكلمات منطوقة فعلاً "
        "في المقطع ومن دون حشو أو تكرار، حتى 85 حرفًا قبل إضافة الهاشتاغ والرقم. "
        "اكتب 3 جمل وصفية موجزة مفيدة للبحث (description_lines) كجمل سليمة، "
        "لا تنسخ التفريغ حرفيًا أو تقسمه لأسطر عشوائية. "
        "أضف 3 إلى 6 هاشتاغات ذات صلة حقيقة بالموضوع، منها عام ومتخصص، "
        "ولا تستخدم كلمات حشو مثل مثلا أو داخل أو يعني. "
        "أضف keywords مصفوفة من 6 إلى 20 عبارة بحث عربية طويلة وقصيرة، "
        "مأخوذة من موضوع الفيديو والمقطع، بدون هاشتاغات، وتأكد أن "
        "مجموع الأحرف مع الفواصل وعلامات الاقتباس الضمنية لعبارات متعددة الكلمات "
        "لا يتجاوز 500 حرف. "
        "لا تذكر رابط المصدر، ولا تخترع تاريخ تصوير أو نشر أو بلد دعم أو رسوم. "
        "أعد JSON بهذه المفاتيح فقط: title, description_lines (3), "
        "hashtags (3-6), keywords (6-20). لا تقدم عناوين بديلة. "
        "اقتراح سابق غير موثوق؛ لا تنسخه لو مبتور: " + str(suggested_title or "") +
        "\n\nسياق تفريغ الفيديو الكامل:\n" + whole_text +
        "\n\nالكلام الحقيقي داخل الريلز المختار فقط:\n" + clip_text
    )

    def checked(payload):
        result = _validate(payload, clip_text=clip_text, full_context=whole_text)
        if clip_number is not None:
            result["title"] = numbered_seo_title(
                result["title"], result["hashtags"], clip_number, clip_text)
        return result

    try:
        result, model = generate_json(
            prompt, (api_key or "").strip(), "REEL_METADATA_MODELS", checked
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "REEL_SEO_UNAVAILABLE: لا يوجد عنوان ووصف وكلمات بحث موثوقة؛ "
            "لن تُعتمد أو تُنشر معاينة ببيانات ضعيفة. " + str(exc)
        ) from exc
    result["method"] = model
    return result
