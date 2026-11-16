
import os
import math
import re
import glob
import json
import subprocess
import traceback
import time
import shutil
import hashlib
from datetime import datetime, timezone
from approved_publish import publish_approved_clips
from reel_metadata import generate_reel_metadata, parse_youtube_tags
from reel_full_plan import build_remaining_plan
from reel_video_layout import ffprobe_layout, layout_filter
from reel_topics import build_topic_plan
from reel_transcript_store import (store_clip_transcripts, load_clip_transcripts,
                                   store_caption_cues, load_caption_cues)
from direct_publish import publish_direct
from reel_history import load_history, has_overlap, published_windows
from source_video_info import original_youtube_id, read_source_facts, preview_fact_lines
from telegram_reel_details import format_reel_details
from text_ai import generate_json
from youtube_source import download_youtube_video
from drive_source import is_drive_url, download_drive_video
from pathlib import Path
from urllib.parse import unquote
import requests
from urllib.parse import urlparse, parse_qs
from google import genai
from transcription_pipeline import obtain_timed_transcript, cues_to_text, clip_has_coverage, save_captions
from captions_renderer import burn_captions
from caption_revisions import apply_approved_edits
from transcript_proofreader import proofread_timed_cues, preserve_approved_word_edits
from reel_preview_settings import resolve_preview_settings
from clip_planner import (build_clip_plan, format_clip_plan, save_clip_plan, source_duration,
                          load_clip_plan, plan_reference, parse_clip_choices, record_preview_result)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 1. استدعاء المفاتيح السرية من GitHub Secrets
# ==========================================
VIDEO_URL = os.getenv("VIDEO_URL", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
YT_COOKIES = os.getenv("YT_COOKIES", "").strip()
TELEGRAM_FILE_ID = os.getenv("TELEGRAM_FILE_ID", "").strip()
AGENT_ACTION = os.getenv("AGENT_ACTION", "prepare").strip().lower()
TRANSCRIPT_SOURCE = "unknown"

# مفاتيح فيسبوك (اختيارية)
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()

# مفاتيح يوتيوب (لرفع الفيديو تلقائياً على قناتك)
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "").strip()
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "").strip()
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "").strip()

# تجهيز ملف الكوكيز إذا كان موجوداً في الخزنة
COOKIES_FILE = None
if YT_COOKIES:
    COOKIES_FILE = "cookies.txt"
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(YT_COOKIES.replace("\\n", "\n") + "\n")
    os.chmod(COOKIES_FILE, 0o600)


def fetch_telegram_source():
    """Fetch a user-supplied MP4 using Telegram Bot API (cloud getFile limit: 20 MB)."""
    if not TELEGRAM_FILE_ID:
        return
    if not TG_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN missing for file download")
    api = f"https://api.telegram.org/bot{TG_TOKEN}"
    response = requests.get(f"{api}/getFile", params={"file_id": TELEGRAM_FILE_ID}, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram getFile rejected the file: " + str(payload.get("description", "")))
    meta = payload["result"]
    if meta.get("file_size", 0) > 20 * 1024 * 1024:
        raise RuntimeError("TELEGRAM_FILE_TOO_LARGE: Telegram cloud Bot API download is limited to 20 MB; use a smaller MP4 or a local Bot API server")
    path = meta["file_path"]
    url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{path}"
    with requests.get(url, stream=True, timeout=(20, 120)) as r:
        r.raise_for_status()
        with open("telegram_source.mp4", "wb") as out:
            total = 0
            for block in r.iter_content(1024 * 1024):
                total += len(block)
                if total > 20 * 1024 * 1024:
                    raise RuntimeError("Telegram MP4 exceeds 20 MB")
                out.write(block)
    source = Path("telegram_source.mp4")
    if source.stat().st_size == 0:
        raise RuntimeError("TELEGRAM_MP4_EMPTY: downloaded file is empty")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
                           capture_output=True, text=True, timeout=30)
    if probe.returncode != 0 or not probe.stdout.strip():
        raise RuntimeError("TELEGRAM_MP4_INVALID: ffprobe could not read the downloaded MP4")
    os.environ["SOURCE_VIDEO_FILE"] = str(source.resolve())
    print("SOURCE: TELEGRAM_MP4; FILE_RECEIVED: YES; FILE_VALIDATED: YES; YOUTUBE_DOWNLOAD: SKIPPED")


# ==========================================
# 2. دوال إرسال الرسائل والفيديو إلى تيليجرام
# ==========================================
def fetch_drive_source():
    """Resolve a shared Google Drive video for a prepare or preview job."""
    if not is_drive_url(VIDEO_URL):
        return
    if TELEGRAM_FILE_ID:
        raise RuntimeError("DRIVE_SOURCE_CONFLICT: attach a Drive link alone")
    path = download_drive_video(VIDEO_URL)
    os.environ["SOURCE_VIDEO_FILE"] = str(path.resolve())


def _ui_fingerprint(plan):
    """Same short plan identity used by the Cloudflare callback handler."""
    return hashlib.sha256(plan_reference(plan).encode('utf-8')).hexdigest()[:12]


def _proposals_keyboard(plan):
    ref = _ui_fingerprint(plan)
    rows = []
    for clip in plan['clips']:
        duration = round(clip['end_time'] - clip['start_time'])
        rows.append([{'text': f"▫️ {clip['id']}. {clip['title'][:37]} · {duration}ث",
                      'callback_data': f"p:toggle:{ref}:{clip['id']}"}])
    rows.append([{'text': '🎞️ معاينة المقاطع المختارة',
                  'callback_data': f'p:preview:{ref}'}])
    rows.append([{'text': '📊 الحالة', 'callback_data': f'p:status:{ref}'},
                 {'text': '🏠 القائمة', 'callback_data': f'p:menu:{ref}'}])
    return {'inline_keyboard': rows}


def _preview_keyboard(plan, chosen_ids):
    ref = _ui_fingerprint(plan)
    # Cloudflare Worker has the one-tap callback in production.
    # Explicit approval directly queues the upload, with no second confirmation.
    action = 'approvepublish'
    label = '✅ اعتماد ونشر فورًا للمقطع ' + '، '.join(map(str, chosen_ids))
    return {'inline_keyboard': [
        [{'text': label,
          'callback_data': f'p:{action}:{ref}:{os.getenv("GITHUB_RUN_ID", "")}'}],
        [{'text': '🎬 تجربة مقطع آخر', 'callback_data': f'p:menu:{ref}'}]
    ]}


def send_telegram(msg, reply_markup=None, parse_mode=None):
    """إرسال رسالة نصية إلى تيليجرام مع طباعتها في سجل GitHub"""
    print(msg)
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️ تنبيه: مفاتيح تيليجرام غير موجودة في Secrets!")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    if parse_mode and len(msg) > 4000:
        raise RuntimeError('TELEGRAM_FORMATTED_MESSAGE_TOO_LONG')
    for i in range(0, len(msg), 4000):
        payload = {"chat_id": TG_CHAT_ID, "text": msg[i:i+4000]}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup and i + 4000 >= len(msg):
            payload["reply_markup"] = reply_markup
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram refused sendMessage")


def send_telegram_video(video_path, caption=""):
    """إرسال فيديو الريلز المقصوص إلى تيليجرام"""
    if not TG_TOKEN or not TG_CHAT_ID:
        raise RuntimeError("TELEGRAM_PREVIEW_DELIVERY_NOT_CONFIGURED")
    if not os.path.isfile(video_path):
        raise RuntimeError("TELEGRAM_PREVIEW_VIDEO_MISSING")
    if os.path.getsize(video_path) > 50 * 1024 * 1024:
        raise RuntimeError("TELEGRAM_PREVIEW_VIDEO_TOO_LARGE: >50MB")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo"
    with open(video_path, 'rb') as video_file:
        response = requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "caption": caption[:1000], "supports_streaming": "true"},
            files={"video": video_file},
            timeout=120
        )
    response.raise_for_status()
    if not response.json().get('ok'):
        raise RuntimeError('TELEGRAM_PREVIEW_DELIVERY_REJECTED')
    print(f'PREVIEW_DELIVERED: YES; BYTES: {os.path.getsize(video_path)}')


# ==========================================
# 3. دوال استخراج معرف ونص الفيديو (ثلاثية المراحل)
# ==========================================
def extract_video_id(url):
    """استخراج معرف الفيديو من جميع صيغ روابط يوتيوب"""
    parsed = urlparse(url)
    if parsed.hostname in ('youtu.be', 'www.youtu.be'):
        return parsed.path[1:].split('?')[0]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query)['v'][0]
        if parsed.path.startswith(('/shorts/', '/live/', '/embed/')):
            return parsed.path.split('/')[2].split('?')[0]
    raise ValueError(f"رابط يوتيوب غير صالح: {url}")


def get_transcript(video_id, url):
    """محرك ثلاثي المراحل لسحب النص والترجمة وتخطي حظر IP السحابة"""
    global TRANSCRIPT_SOURCE
    TRANSCRIPT_SOURCE = "unknown"

    # المرحلة 1: استخدام yt-dlp (مع الكوكيز إن وجدت) لسحب ملف الترجمة
    try:
        cmd = [
            "yt-dlp",
            "--write-auto-sub", "--write-sub",
            "--sub-langs", "ar,en",
            "--skip-download",
            "-o", "sub_file"
        ]
        if COOKIES_FILE:
            cmd.extend(["--cookies", COOKIES_FILE])
        else:
            pass  # Let updated yt-dlp choose supported default clients
        cmd.append(url)

        subprocess.run(cmd, capture_output=True, text=True, timeout=65)
        sub_files = glob.glob("sub_file*.vtt")
        if sub_files:
            with open(sub_files[0], "r", encoding="utf-8") as f:
                raw_vtt = f.read()
            clean_text = re.sub(r'<[^>]+>', '', raw_vtt)
            lines = []
            for line in clean_text.splitlines():
                if not line.startswith(("WEBVTT", "Kind:", "Language:")) and "-->" not in line and line.strip():
                    if not lines or lines[-1] != line.strip():
                        lines.append(line.strip())
            if lines:
                TRANSCRIPT_SOURCE = "subtitles"
                print("✅ تم سحب الكابشن بنجاح عبر المرحلة الأولى (yt-dlp)")
                return "\n".join(lines)[:20000]
    except Exception as e:
        print(f"Stage 1 sub failed: {e}")

    # المرحلة 2: استخدام خوادم Invidious البديلة
    invidious_instances = [
        "https://inv.tux.pizza",
        "https://invidious.nerdvpn.de",
        "https://invidious.privacyredirect.com"
    ]
    for instance in invidious_instances:
        try:
            r = requests.get(f"{instance}/api/v1/videos/{video_id}", timeout=10)
            if r.status_code == 200:
                vdata = r.json()
                title = vdata.get("title", "")
                desc = vdata.get("description", "")
                TRANSCRIPT_SOURCE = "description"
                print(f"✅ تم سحب بيانات الفيديو الكاملة عبر المرحلة الثانية ({instance})")
                return f"عنوان الفيديو: {title}\nتفاصيل ومحتوى الفيديو:\n{desc}"[:20000]
        except Exception:
            continue

    # المرحلة 3: خطة الطوارئ عبر NoEmbed
    try:
        r = requests.get(f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}", timeout=10).json()
        TRANSCRIPT_SOURCE = "title"
        print("✅ تم سحب العنوان عبر المرحلة الثالثة (NoEmbed)")
        return f"عنوان الفيديو الأصلي: {r.get('title', '')} - القناة: {r.get('author_name', '')}"
    except Exception:
        TRANSCRIPT_SOURCE = "url"
        return f"رابط الفيديو: {url}"


def parse_gemini_json(raw):
    """Parse one JSON object, tolerating Markdown fences or surrounding commentary.

    Multiple objects are rejected instead of silently selecting potentially wrong data.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("GEMINI_EMPTY_RESPONSE: Gemini returned no text")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.I)
        text = re.sub(r"\s*```$", "", text, count=1).strip()
    decoder = json.JSONDecoder()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        objects = []
        for match in re.finditer(r"\{", text):
            try:
                obj, consumed = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                objects.append((obj, match.start(), match.start() + consumed))
        # Reject any ambiguous second object; don't silently accept extra JSON.
        if len(objects) != 1:
            raise ValueError("GEMINI_INVALID_JSON: expected exactly one JSON object")
        parsed = objects[0][0]
    if not isinstance(parsed, dict):
        raise ValueError("GEMINI_INVALID_JSON: response must be a JSON object")
    return parsed


def validate_gemini_data(data):
    """Validate fields used by SEO and video clipping before any side effects."""
    for key in ("long_titles", "short_titles"):
        value = data.get(key)
        if not isinstance(value, list) or len(value) != 3 or any(not isinstance(x, str) or not x.strip() for x in value):
            raise ValueError(f"GEMINI_INVALID_FIELD: {key} must contain exactly 3 nonempty titles")
    for key in ("description_block", "tags_string", "quora_ar", "quora_en"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"GEMINI_INVALID_FIELD: {key} must be nonempty text")
    for key in ("start_time", "end_time"):
        if type(data.get(key)) is not int:
            raise ValueError(f"GEMINI_INVALID_FIELD: {key} must be an integer")
    start, end = data["start_time"], data["end_time"]
    if start < 0 or end - start < 30 or end - start > 55:
        raise ValueError("GEMINI_INVALID_INTERVAL: clip must be between 30 and 55 seconds")
    if len(data["tags_string"]) > 500:
        raise ValueError("GEMINI_INVALID_TAGS: tags_string exceeds 500 characters")
    return data


# ==========================================
# 4. دالة الذكاء الاصطناعي وتوليد السيو (Gemini 3.1)
# ==========================================
def analyze_with_gemini(transcript_text, video_url):
    """تحليل النص وتوليد جميع عناصر السيو ومنشورات Quora عبر Gemini 3.1"""
    if not GEMINI_KEY and not os.getenv("GROQ_API_KEY", "").strip():
        raise ValueError("لا يوجد مفتاح لتوليد النص من Gemini أو Groq")

    prompt = f"""
    أنت خبير سيو يوتيوب ومونتاج. بناءً على بيانات/نص الفيديو التالي:
    {transcript_text}

    المطلوب إخراج ملف JSON صالح فقط يحتوي على المفاتيح التالية بدقة تامة:
    1. "start_time": ثانية البداية لأقوى مقطع يصلح كفيديو ريلز قصير (رقم صحيح Integer مثل 10).
    2. "end_time": ثانية النهاية للمقطع بحيث تكون المدة بين 30 إلى 55 ثانية (رقم صحيح Integer مثل 50).
    3. "long_titles": مصفوفة نصية تحتوي على 3 عناوين طويلة قوية للسيو.
    4. "short_titles": مصفوفة نصية تحتوي على 3 عناوين قصيرة جذابة للريلز والشورتس.
    5. "description_block": نص واحد يتكون من 3 أسطر فقط تحتوي على عبارات بحثية مصاغة في جمل مدمجة مع الوصف، يليه في نفس البلوك هاشتاجات عامة ومتخصصة (بدون فواصل زمنية نهائياً).
    6. "tags_string": كلمات مفتاحية (جمل طويلة وقصيرة للمحتوى) مفصولة بفاصلة واحدة (,)، بشرط ألا يتجاوز إجمالي عدد الحروف 500 حرف نهائياً.
    7. "quora_ar": منشور احترافي لمنصة Quora باللغة العربية يلتزم بسياسة النشر، مدمج بداخل نصه رابط الفيديو ({video_url})، ويحتوي على هاشتاج واحد فقط.
    8. "quora_en": منشور احترافي لمنصة Quora باللغة الإنجليزية يلتزم بسياسة النشر، مدمج بداخل نصه رابط الفيديو ({video_url})، ويحتوي على هاشتاج واحد فقط.
    """

    result, provider = generate_json(
        prompt, GEMINI_KEY, 'SEO_MODELS', validate_gemini_data)
    print('SEO_GENERATOR: ' + provider)
    return result


# ==========================================
# 5. دالة التحميل والمونتاج إلى ريلز (9:16)
# ==========================================
def download_and_convert_to_reel(url, start_sec, end_sec, blur_mode='auto'):
    """Download with bounded retries, or accept a Telegram-supplied local MP4."""
    source = Path(os.getenv("SOURCE_VIDEO_FILE", "").strip())
    target = Path("input.mp4")
    if source.is_file():
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        print("SOURCE: LOCAL_MP4; YOUTUBE_DOWNLOAD: SKIPPED")
    else:
        if os.getenv("TELEGRAM_FILE_ID", "").strip():
            raise RuntimeError("TELEGRAM_MP4_UNAVAILABLE: a Telegram file ID was supplied but no validated local MP4 is available; refusing YouTube fallback")
        print("SOURCE: YOUTUBE_DOWNLOAD; TELEGRAM_FILE_ID: ABSENT")
        target = download_youtube_video(url, COOKIES_FILE)
        print("SOURCE: YOUTUBE_DOWNLOAD; VIDEO_RECEIVED: YES")
    probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                            '-of','default=noprint_wrappers=1:nokey=1',str(target)],
                           capture_output=True,text=True,check=True)
    length = float(probe.stdout.strip())
    start, end = float(start_sec), float(end_sec)
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError('Invalid reel interval: non-finite timestamp')
    duration = end - start
    if start < 0 or start >= length or duration < 15 or duration > 180 or end > length + 0.15:
        raise ValueError(f'Invalid reel interval: start={start}, end={end}, source duration={length:.1f}')
    duration = min(duration, length-start)
    if duration < 14.9:
        raise ValueError('Source video is too short for the requested reel interval')
    width, height, ratio = ffprobe_layout(target)
    chosen_filter, render_mode = layout_filter(width, height, ratio, blur_mode=blur_mode)
    print(f"REEL_SOURCE_LAYOUT: {width}x{height}; ASPECT={ratio:.4f}; "
          f"MODE={render_mode}; BLUR={'NO' if render_mode == 'native_vertical_no_blur' else 'YES'}")
    ffmpeg_cmd = ['ffmpeg','-hide_banner','-y','-ss',str(start),'-i',str(target),'-t',str(duration)]
    if render_mode != 'letterbox_blurred_background':
        ffmpeg_cmd += ['-vf', chosen_filter, '-map','0:v:0']
    else:
        ffmpeg_cmd += ['-filter_complex',chosen_filter,'-map','[v]']
    ffmpeg_cmd += ['-map','0:a:0?',
                  '-c:v','libx264','-preset','fast','-crf','23','-pix_fmt','yuv420p',
                  '-c:a','aac','-b:a','128k','-movflags','+faststart','output_reel.mp4']
    subprocess.run(ffmpeg_cmd,check=True,timeout=900)
    if not Path('output_reel.mp4').is_file() or Path('output_reel.mp4').stat().st_size < 10000:
        raise RuntimeError('Output MP4 missing or empty')


# ==========================================
# 6. دوال النشر التلقائي (فيسبوك + يوتيوب)
# ==========================================
def upload_to_facebook(video_path, title, description):
    """رفع الفيديو على صفحة فيسبوك عبر Graph API"""
    if not FB_PAGE_ID or not FB_TOKEN:
        return "⏭️ فيسبوك: لم يتم ضبط المفاتيح بعد (تم التخطي)."

    try:
        url = f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos"
        caption = f"{title}\n\n{description}"
        with open(video_path, 'rb') as f:
            files = {'source': f}
            data = {'title': title, 'description': caption, 'access_token': FB_TOKEN}
            r = requests.post(url, files=files, data=data, timeout=120)
        if r.status_code == 200:
            return "✅ فيسبوك: تم النشر بنجاح!"
        return f"⚠️ فيسبوك: فشل الرفع ({r.text})"
    except Exception as e:
        return f"❌ فيسبوك خطأ: {str(e)}"


def upload_to_youtube(video_path, title, description, tags_str, *,
                      publish_at=None, recording_date=None, privacy=None):
    """رفع الفيديو كـ YouTube Shorts مع العنوان والوصف والتاجز"""
    if not YT_CLIENT_ID or not YT_CLIENT_SECRET or not YT_REFRESH_TOKEN:
        return "⏭️ يوتيوب: لم يتم ضبط المفاتيح بعد (تم التخطي)."

    try:
        creds = Credentials(
            None,
            refresh_token=YT_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET
        )
        youtube = build("youtube", "v3", credentials=creds)
        clean_tags = parse_youtube_tags(tags_str)

        # Both optional fields are supplied before upload; never try to
        # rewrite the real YouTube publishedAt property.
        if recording_date:
            from reel_schedule import validate_recording_date
            recording_date = validate_recording_date(recording_date)
        if publish_at:
            from datetime import datetime as _dt, timezone as _tz
            scheduled = _dt.fromisoformat(publish_at.replace("Z", "+00:00"))
            if scheduled.tzinfo is None or scheduled <= _dt.now(_tz.utc):
                raise ValueError("REEL_SCHEDULE_MUST_BE_IN_FUTURE")
        if privacy is not None and privacy not in ("public", "private", "unlisted"):
            raise ValueError("REEL_PRIVACY_INVALID")
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": clean_tags,
                "categoryId": "22",
                "defaultLanguage": "ar"
            },
            "status": {
                "privacyStatus": privacy or os.getenv("YT_PRIVACY_STATUS", "public"),
                "selfDeclaredMadeForKids": False
            }
        }

        parts = "snippet,status"
        if recording_date:
            body["recordingDetails"] = {"recordingDate": recording_date}
            parts += ",recordingDetails"
        if publish_at:
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = publish_at
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part=parts, body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        # Uploading a private video is not the same as publishing it publicly.
        completed_utc = datetime.now(timezone.utc).isoformat()
        original_api_stamp = response.get("snippet", {}).get("publishedAt")
        details = ("وقت الرفع لدى يوتيوب: " + original_api_stamp) if original_api_stamp else (
            "وقت اكتمال طلب الرفع (UTC): " + completed_utc)
        public_note = ("موعد النشر العام المجدول: " + publish_at) if publish_at else (
            "حالة الظهور عند الرفع: " + body['status']['privacyStatus'] + " (قد تستغرق المعالجة وقتًا).")
        return (f"✅ يوتيوب: تم رفع الفيديو بنجاح بوضع {body['status']['privacyStatus']} "
                f"(https://youtu.be/{response['id']})\n"
                f"{details}\n{public_note}")
    except Exception as e:
        return f"❌ يوتيوب خطأ: {str(e)}"


# ==========================================
# 7. نقطة التشغيل الرئيسية (Main Execution)
# ==========================================
def prepare_selected_previews(plan, chosen_ids, expected_ref):
    """Prepare verified, captioned previews in this same GitHub Actions run.

    The user must still explicitly authorize any YouTube upload.
    """
    clips_by_id = {item['id']: item for item in plan['clips']}
    if AGENT_ACTION == 'preview':
        send_telegram('بدأ تجهيز معاينة المقاطع: ' + ', '.join(map(str, chosen_ids)))
    # During automatic /prepare, the original source is already downloaded.
    # Do not fetch the same Google Drive/Telegram video for a second time.
    cached_source = os.getenv('SOURCE_VIDEO_FILE', '').strip()
    if not cached_source or not Path(cached_source).is_file():
        fetch_telegram_source()
        fetch_drive_source()
    preview_dir = Path('previews')
    preview_dir.mkdir(exist_ok=True)
    preview_manifest = {'plan_ref': expected_ref, 'preview_run_id': os.getenv('GITHUB_RUN_ID', ''),
                        'video_url': VIDEO_URL, 'telegram_file_id': TELEGRAM_FILE_ID,
                        'previewed_ids': chosen_ids, 'files': {}}
    preview_cues = None  # One local-source transcription for all chosen clips.
    preview_metadata = {}
    source_facts = None
    effective_settings = resolve_preview_settings(
        plan, chosen_ids, os.getenv('CLIP_OPTIONS_JSON', ''))
    for clip_id in chosen_ids:
        if plan_reference(load_clip_plan()) != expected_ref:
            raise RuntimeError('CLIP_PLAN_CHANGED: source replaced during preview')
        clip = clips_by_id[clip_id]
        settings = effective_settings[clip_id]
        download_and_convert_to_reel(VIDEO_URL, clip['start_time'], clip['end_time'],
                                     blur_mode=settings.get('blur', 'auto'))
        if preview_cues is None:
            # Stage 3: trust the audio of the MP4 being cut, never title-only text.
            if plan.get('method') == 'topic_transcript_ai':
                preview_cues = (load_caption_cues(plan) or
                                load_clip_transcripts(plan).get('cues', []))
                preview_source = 'saved_caption_cues'
            else:
                preview_cues, preview_source = obtain_timed_transcript(
                    VIDEO_URL, video_path='input.mp4', cookies_file=COOKIES_FILE,
                    prefer_local_audio=True, prefer_word_timestamps=True)
            if not preview_cues:
                raise RuntimeError('CAPTIONS_UNAVAILABLE: no timed transcription for the selected source; cannot send an uncaptioned preview')
            print(f'PREVIEW_TRANSCRIPTION: {preview_source}; CUES: {len(preview_cues)}')
            # Match the final upload's saved caption cues to the exact preview.
            original_cues = preview_cues
            approved_cues = apply_approved_edits(original_cues, VIDEO_URL)
            if plan.get('caption_cues_prepared'):
                preview_cues = approved_cues
                print('CAPTION_PROOFREAD: SAVED_REVIEW_REUSED; NO_RETRANSCRIPTION')
            else:
                # Audio-grounded proofread: Gemini -> Deepgram -> AssemblyAI.
                reviewed_cues = proofread_timed_cues(original_cues)
                preview_cues = preserve_approved_word_edits(
                    original_cues, reviewed_cues, approved_cues)
            if plan.get('method') == 'topic_transcript_ai':
                latest = load_clip_plan()
                if plan_reference(latest) != expected_ref:
                    raise RuntimeError('CAPTION_PLAN_CHANGED_DURING_REVIEW')
                store_caption_cues(latest, preview_cues)
                latest['caption_cues_prepared'] = True
                save_clip_plan(latest)
                plan = latest
                print('CAPTION_PREVIEW_UPLOAD_PARITY: SAVED')
            # Read original YouTube dates from the official API only if a
            # YouTube link is the source; Drive never implies an upload date.
            youtube_client = None
            if original_youtube_id(VIDEO_URL) and all((
                    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)):
                try:
                    auth = Credentials(
                        None, refresh_token=YT_REFRESH_TOKEN,
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=YT_CLIENT_ID,
                        client_secret=YT_CLIENT_SECRET)
                    youtube_client = build("youtube", "v3", credentials=auth)
                except Exception as exc:
                    print("SOURCE_YOUTUBE_METADATA_UNAVAILABLE: " + type(exc).__name__)
            source_facts = read_source_facts(
                VIDEO_URL, cues_to_text(preview_cues), youtube_client)
        captioned_preview = (
            burn_captions('output_reel.mp4', preview_cues,
                          clip['start_time'], clip['end_time'])
            if settings.get('captions', True) else 'output_reel.mp4')
        # Fail closed on broken dimensions, before offering or approving a preview.
        dimensions = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'json',
             captioned_preview],
            check=True, capture_output=True, text=True, timeout=30)
        streams = json.loads(dimensions.stdout).get('streams', [])
        if len(streams) != 1 or streams[0].get('width') != 1080 or streams[0].get('height') != 1920:
            raise RuntimeError('REEL_DIMENSIONS_INVALID: expected 1080x1920')
        if has_overlap(load_history(VIDEO_URL), clip['start_time'], clip['end_time']):
            raise RuntimeError('REEL_ALREADY_UPLOADED: معاينة المقطع متداخلة مع ريلز منشور من نفس المصدر')
        if (plan.get('method') == 'topic_transcript_ai'
                and clip.get('reel_seo_version') == 3):
            reel_meta = {
                'title': clip['reel_title'],
                'description': clip['reel_description'],
                'tags_string': clip['reel_tags_string'],
                'language': clip['reel_language'],
            }
        else:
            reel_meta = generate_reel_metadata(
                preview_cues, clip['start_time'], clip['end_time'],
                clip['title'], GEMINI_KEY, clip_number=clip.get('serial', clip['id']))
        reel_meta['recording_date'] = (source_facts or {}).get('source_recorded_at')
        reel_meta['source_published_at'] = (source_facts or {}).get('source_published_at')
        preview_metadata[str(clip_id)] = reel_meta
        # Reuse input.mp4 in this same runner for any subsequent selection.
        if Path('input.mp4').is_file():
            os.environ['SOURCE_VIDEO_FILE'] = str(Path('input.mp4').resolve())
        stamp_start = f"{clip['start_time']:.1f}"
        stamp_end = f"{clip['end_time']:.1f}"
        send_telegram_video(captioned_preview,
                            f"معاينة {clip_id}: {reel_meta['title']}\n{stamp_start}s - {stamp_end}s\nخصوصية الرفع: {settings['privacy']}\nلم يُنشر الفيديو")
        send_telegram(
            format_reel_details(clip, reel_meta, source_facts, preview_fact_lines),
            parse_mode="HTML")
        stored = preview_dir / f'clip-{clip_id}.mp4'
        shutil.copyfile(captioned_preview, stored)
        preview_manifest['files'][str(clip_id)] = hashlib.sha256(stored.read_bytes()).hexdigest()
        print(f"CLIP_PREVIEW: ID={clip_id}; START={stamp_start}; END={stamp_end}; "
              f"CAPTIONS_BURNED={'YES' if settings.get('captions', True) else 'NO'}; DELIVERED=YES")
    if not preview_manifest['preview_run_id'].isdigit():
        raise RuntimeError('PREVIEW_RUN_ID_MISSING')
    (preview_dir / 'manifest.json').write_text(
        json.dumps(preview_manifest, ensure_ascii=False), encoding='utf-8')
    record_preview_result(expected_ref, chosen_ids, preview_manifest['preview_run_id'],
                          preview_metadata)
    next_step = 'اضغط اعتماد ونشر فورًا مرة واحدة.'
    send_telegram('✅ المعاينة وبيانات SEO جاهزة للمقطع ' + '، '.join(map(str, chosen_ids)) +
                  '. راجع الفيديو ثم ' + next_step + ' لم يتم النشر.',
                  reply_markup=_preview_keyboard(plan, chosen_ids))


if __name__ == "__main__":
    try:
        if not VIDEO_URL:
            raise ValueError("لم يتم إدخال رابط الفيديو (VIDEO_URL فارغ)!")

        if AGENT_ACTION not in ('seo', 'prepare', 'publish', 'preview', 'direct_publish', 'metadata'):
            raise ValueError('Unsupported AGENT_ACTION; refusing to publish')
        if AGENT_ACTION == 'metadata':
            plan = load_clip_plan()
            if (plan_reference(plan) != os.getenv('CLIP_PLAN_REF', '').strip() or
                    plan.get('method') != 'topic_transcript_ai' or
                    plan.get('video_url') != VIDEO_URL or
                    plan.get('telegram_file_id', '') != TELEGRAM_FILE_ID):
                raise RuntimeError('METADATA_PLAN_CHANGED')
            requested = [int(x) for x in os.getenv('CLIP_IDS','').split(',')
                         if x.strip().isdigit()]
            if not requested or len(requested) != len(set(requested)):
                raise RuntimeError('METADATA_IDS_INVALID')
            cues = load_clip_transcripts(plan).get('cues', [])
            if not cues:
                raise RuntimeError('METADATA_TRANSCRIPT_UNAVAILABLE')
            fixed, failed = [], []
            for clip in plan['clips']:
                if clip['id'] not in requested:
                    continue
                if clip.get('seo_ready'):
                    continue
                if (plan.get('clip_jobs') or {}).get(str(clip['id'])):
                    continue
                try:
                    meta = generate_reel_metadata(
                        cues, clip['start_time'],clip['end_time'],
                        clip['title'], GEMINI_KEY,clip_number=clip['serial'])
                    clip['title'] = meta['title']
                    clip['reel_title'] = meta['title']
                    clip['reel_description'] = meta['description']
                    clip['reel_tags_string'] = meta['tags_string']
                    clip['reel_language'] = meta['language']
                    clip['reel_seo_version'] = 3
                    clip['seo_ready'] = True
                    fixed.append(clip['id'])
                except Exception as error:
                    failed.append(clip['id'])
                    print('METADATA_RETRY_FAILED: ' + type(error).__name__)
            save_clip_plan(plan)
            send_telegram(
                f'✅ تمت إعادة تجهيز SEO للمقاطع: {fixed}. '
                f'ما زالت تحتاج مراجعة: {failed}. افتح القائمة.',
                reply_markup={'inline_keyboard': [[{
                    'text':'🎛️ فتح القائمة',
                    'callback_data':f"p:menu:{_ui_fingerprint(plan)}"
                }]]})
            raise SystemExit(0)

        if AGENT_ACTION == 'direct_publish':
            try:
                publish_direct(
                    os.getenv('CLIP_PLAN_REF', '').strip(),
                    os.getenv('CLIP_IDS', '').strip(),
                    VIDEO_URL, TELEGRAM_FILE_ID,
                    download_and_convert_to_reel, burn_captions,
                    upload_to_youtube, send_telegram,
                    fetch_telegram_source, fetch_drive_source)
            except Exception as exc:
                send_telegram('❌ فشل رفع ريلز معتمد أو تعذر تأكيد نتيجته: ' + str(exc)[:450])
                raise
            raise SystemExit(0)

        if AGENT_ACTION == 'publish':
            try:
                publish_approved_clips(
                    os.getenv('CLIP_PLAN_REF', '').strip(),
                    os.getenv('CLIP_IDS', '').strip(), VIDEO_URL, TELEGRAM_FILE_ID,
                    load_clip_plan, save_clip_plan, upload_to_youtube, send_telegram)
                current = load_clip_plan()
                if (current.get('status') == 'awaiting_selection'
                        and current.get('clips')):
                    send_telegram(
                        f"باقي {len(current['clips'])} ريلز من نفس الفيديو جاهزة للاختيار. "
                        "اضغط الزر لإكمال المعاينات دون إعادة تحليل المصدر.",
                        reply_markup={'inline_keyboard': [[{
                            'text': '🎬 أكمل باقي الريلز',
                            'callback_data': f"p:menu:{_ui_fingerprint(current)}"
                        }]]})
            except Exception as exc:
                send_telegram('❌ تعذر نشر المقطع المعتمد: ' + str(exc)[:500])
                raise
            raise SystemExit(0)

        # Stage 2: resolve existing proposal, validate exact source and IDs, render ONLY selection.
        if AGENT_ACTION == 'preview':
            plan = load_clip_plan()
            expected_ref = os.getenv('CLIP_PLAN_REF', '').strip()
            if not expected_ref or plan_reference(plan) != expected_ref:
                raise RuntimeError('CLIP_PLAN_STALE: requested selection no longer matches the saved suggestions')
            if VIDEO_URL != plan.get('video_url') or TELEGRAM_FILE_ID != plan.get('telegram_file_id', ''):
                raise RuntimeError('CLIP_SOURCE_MISMATCH: request and saved plan have different sources')
            chosen_ids = parse_clip_choices(os.getenv('CLIP_IDS', ''), plan)
            prepare_selected_previews(plan, chosen_ids, expected_ref)
            raise SystemExit(0)

        if AGENT_ACTION == 'seo':
            send_telegram(f"⏳ بدأ الوكيل مهمة SEO للفيديو:\n{VIDEO_URL}")

        fetch_telegram_source()
        fetch_drive_source()

        # 1. Obtain timed speech from the selected video source.
        vid_id = None if is_drive_url(VIDEO_URL) else extract_video_id(VIDEO_URL)
        local_source = os.getenv("SOURCE_VIDEO_FILE", "").strip()
        timed_cues, timed_source = obtain_timed_transcript(
            VIDEO_URL, video_path=local_source or None, cookies_file=COOKIES_FILE,
            prefer_local_audio=is_drive_url(VIDEO_URL))
        timing_grounded = bool(timed_cues)
        if timing_grounded:
            save_captions(timed_cues)
            transcript = cues_to_text(timed_cues)
            print(f"TIMED_TRANSCRIPT: {timed_source}; CUES: {len(timed_cues)}")
        else:
            transcript = ('Google Drive source without timed transcription'
                          if is_drive_url(VIDEO_URL) else get_transcript(vid_id, VIDEO_URL))
            print("TIMED_TRANSCRIPT: UNAVAILABLE; TITLE_ONLY_FALLBACK: YES")

        # One GitHub run now prepares, picks the AI-ranked clip, and sends
        # the complete preview automatically; no extra /clip command required.
        if AGENT_ACTION == 'prepare':
            if not timing_grounded:
                send_telegram('⛔ لا يمكن اقتراح قص موثوق بدون تفريغ مؤقت للكلام. أرسل MP4 مع رابطه.')
                raise SystemExit(0)
            duration = source_duration(local_source or None, timed_cues)
            uploaded_history = load_history(VIDEO_URL)
            max_seconds = int(os.getenv('REEL_MAX_SECONDS','59'))
            plan, clip_texts = build_topic_plan(
                VIDEO_URL, TELEGRAM_FILE_ID, timed_cues, duration,
                uploaded_history, GEMINI_KEY, max_seconds=max_seconds)
            # Save source-aligned full transcript and word timing once; subsequent
            # preview/approval/scheduled uploads reuse it instead of running ASR.
            store_clip_transcripts(plan, clip_texts, timed_cues)
            # The direct-publish path uses these same captions unless an owner
            # first reviews a preview and saves corrected audio-grounded cues.
            store_caption_cues(plan, timed_cues)
            plan['caption_cues_prepared'] = False
            failed_seo = []
            for clip in plan['clips']:
                try:
                    meta = generate_reel_metadata(
                        timed_cues, clip['start_time'],clip['end_time'],
                        clip['title'], GEMINI_KEY, clip_number=clip['serial'])
                    clip['title'] = meta['title']
                    clip['reel_title'] = meta['title']
                    clip['reel_description'] = meta['description']
                    clip['reel_tags_string'] = meta['tags_string']
                    clip['reel_language'] = meta['language']
                    clip['reel_seo_version'] = 3
                    clip['seo_ready'] = True
                except Exception as exc:
                    failed_seo.append(clip['id'])
                    clip['seo_ready'] = False
                    print(f"REEL_SEO_PENDING: {clip['id']}; {type(exc).__name__}")
            # The first menu shows topic titles immediately. Never create and
            # send an unwanted preview before the owner selects it.
            save_clip_plan(plan)
            preview = '\n'.join(
                f"{c['id']}. {c['title']} — {int(c['end_time']-c['start_time'])}ث "
                f"({c['start_time']:.0f}–{c['end_time']:.0f})"
                for c in plan['clips'][:8])
            send_telegram(
                f"✅ تم تفريغ الفيديو وتقسيمه إلى {len(plan['clips'])} موضوع مكتمل.\n"
                f"{preview}\n"
                + (f"⚠️ بيانات بعض الريلز تحتاج إعادة توليد: {failed_seo}.\n" if failed_seo else "")
                + "افتح القائمة لمراجعة النصوص والعناوين واختيار النشر أو الجدولة. "
                  "لا يوجد نشر تلقائي ولا معاينة إجبارية.",
                reply_markup={'inline_keyboard': [[{
                    'text':'🎛️ فتح قائمة الريلز',
                    'callback_data':f"p:menu:{_ui_fingerprint(plan)}"
                }]]})
            raise SystemExit(0)

        # /seo is the only command that requests descriptive metadata.
        data = analyze_with_gemini(transcript, VIDEO_URL)
        if not timing_grounded:
            send_telegram("⚠️ لا توجد ترجمة مؤقتة موثوقة؛ السيو مؤقت ولا يسمح بالقص.")
        tags_500 = data['tags_string']
        chosen_title = data['short_titles'][0]
        description_block = data['description_block']

        # 3. إرسال حزمة السيو ومنشورات Quora إلى هاتفك فوراً
        seo_msg = (
            f"🎯 العناوين الطويلة (3):\n"
            f"1. {data['long_titles'][0]}\n"
            f"2. {data['long_titles'][1]}\n"
            f"3. {data['long_titles'][2]}\n\n"
            f"⚡ العناوين القصيرة (3):\n"
            f"1. {data['short_titles'][0]}\n"
            f"2. {data['short_titles'][1]}\n"
            f"3. {data['short_titles'][2]}\n\n"
            f"📝 الوصف والهاشتاجات (بلوك واحد للنسخ):\n"
            f"{description_block}\n\n"
            f"🏷️ الكلمات المفتاحية ({len(tags_500)} حرف):\n"
            f"{tags_500}"
        )
        send_telegram(seo_msg)
        # Quora drafts are not sent unless a separate future command requests them.

        # SEO-only must never fetch, trim, or publish video.
        if AGENT_ACTION == 'seo':
            send_telegram('اكتمل إعداد البيانات الوصفية فقط. لم يتم قص أو نشر فيديو.')
            raise SystemExit(0)


    except Exception as e:
        traceback.print_exc()
        send_telegram(f"❌ تعذر إكمال المهمة ({type(e).__name__}): {str(e)[:300]}. راجع سجل GitHub Actions للتفاصيل.")
        exit(1)
