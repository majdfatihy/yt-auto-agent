"""Original-video facts: YouTube API fields only; never infer a recording date.

Google Drive videos have no YouTube publication date unless a separate verified
YouTube source is given. Audio language derived from transcript is marked inferred.
"""
from urllib.parse import urlparse, parse_qs
from reel_metadata import spoken_language_from_transcript


def original_youtube_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        for prefix in ("/shorts/", "/live/", "/embed/"):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix):].split("/")[0]
    return None


def read_source_facts(video_url, transcript_text, youtube_client=None):
    inferred = spoken_language_from_transcript(transcript_text)
    facts = {
        "source_platform": "YouTube" if original_youtube_id(video_url) else "Google Drive / MP4",
        "source_published_at": None,
        "source_recorded_at": None,
        "source_title": None,
        "source_audio_language": None,
        "speech_language_inferred": inferred,
    }
    video_id = original_youtube_id(video_url)
    if not video_id or youtube_client is None:
        return facts
    try:
        response = youtube_client.videos().list(part="snippet,recordingDetails", id=video_id).execute()
        entry = (response.get("items") or [None])[0]
        if not entry:
            return facts
        snippet = entry.get("snippet") or {}
        recording = entry.get("recordingDetails") or {}
        facts["source_title"] = snippet.get("title")
        facts["source_published_at"] = snippet.get("publishedAt")
        facts["source_audio_language"] = snippet.get("defaultAudioLanguage")
        facts["source_recorded_at"] = recording.get("recordingDate")
    except Exception as exc:
        print("ORIGINAL_VIDEO_FACTS_UNAVAILABLE: " + type(exc).__name__)
    return facts


def preview_fact_lines(facts):
    def known(field):
        return str(facts.get(field) or "غير متاح/غير موثق")
    language = facts.get("source_audio_language")
    if language:
        audio_line = language + " (بيانات مصدر يوتيوب)"
    elif facts.get("speech_language_inferred"):
        audio_line = facts["speech_language_inferred"] + " (مستنتجة من التفريغ، غير مؤكدة من يوتيوب)"
    else:
        audio_line = "غير متاحة"
    return (
        "نوع المصدر: " + known("source_platform") + "\n"
        "تاريخ نشر الفيديو الأصلي: " + known("source_published_at") + "\n"
        "تاريخ تسجيل الفيديو الأصلي: " + known("source_recorded_at") + "\n"
        "لغة الصوت: " + audio_line + "\n"
        "لغة العنوان والوصف الجديد: العربية (ar)\n"
        "تاريخ رفع الريلز الجديد: لا يتحدد إلا بعد الرفع، وليس تاريخ نشره للعامة."
    )
