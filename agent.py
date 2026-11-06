import os
import math
import re
import glob
import json
import subprocess
import traceback
import time
import shutil
import base64
import binascii
from pathlib import Path
from urllib.parse import unquote
import requests
from urllib.parse import urlparse, parse_qs
from google import genai
from transcription_pipeline import obtain_timed_transcript, cues_to_text, clip_has_coverage, save_captions
from captions_renderer import burn_captions
from caption_revisions import apply_approved_edits
from transcript_proofreader import proofread_timed_cues, preserve_approved_word_edits
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

# Parse YouTube cookies without logging their contents. Accept Netscape text or base64.
def prepare_youtube_cookies(raw, destination="cookies.txt"):
    if not raw.strip():
        print("YOUTUBE_COOKIES: NOT_CONFIGURED")
        return None
    content = raw.strip().replace("\\n", "\n").lstrip("\ufeff")
    if not (content.startswith("#") or "\t" in content):
        try:
            content = base64.b64decode("".join(content.split()), validate=True).decode("utf-8-sig")
        except (ValueError, UnicodeError, binascii.Error):
            print("YOUTUBE_COOKIES: INVALID_FORMAT; EXPECTED: NETSCAPE_OR_BASE64")
            return None
    entries = []
    for line in content.splitlines():
        data = line.removeprefix("#HttpOnly_")
        if not data or data.startswith("#"):
            continue
        fields = data.split("\t")
        if len(fields) != 7 or fields[1].upper() not in ("TRUE", "FALSE") or fields[3].upper() not in ("TRUE", "FALSE"):
            continue
        try:
            expires = int(fields[4])
        except ValueError:
            continue
        entries.append((fields[0].lower(), fields[5], expires))
    youtube_entries = [item for item in entries if item[0].lstrip(".") in ("youtube.com", "www.youtube.com", "music.youtube.com")]
    if not entries or not youtube_entries:
        print("YOUTUBE_COOKIES: INVALID_OR_NON_YOUTUBE; EXPECTED: NETSCAPE_COOKIE_LINES")
        return None
    session_keys = {"SID", "HSID", "SSID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"}
    active = [c for c in youtube_entries if c[2] <= 0 or c[2] > time.time()]
    has_login = any(c[1] in session_keys for c in active)
    lines = content.strip().splitlines()
    if not lines or not lines[0].startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        content = "# Netscape HTTP Cookie File\n" + content
    path = Path(destination)
    path.write_text(content.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(f"YOUTUBE_COOKIES: NETSCAPE_OK; YOUTUBE_ENTRIES: {len(youtube_entries)}; ACTIVE: {len(active)}; LOGIN_MARKERS: {'YES' if has_login else 'NO'}")
    return str(path)


# YouTube cookies from GitHub Secrets (raw Netscape or base64-encoded Netscape).
COOKIES_FILE = prepare_youtube_cookies(YT_COOKIES)


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
def send_telegram(msg):
    """إرسال رسالة نصية إلى تيليجرام مع طباعتها في سجل GitHub"""
    print(msg)
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️ تنبيه: مفاتيح تيليجرام غير موجودة في Secrets!")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(msg), 4000):
        response = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg[i:i+4000]}, timeout=15)
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
        if os.getenv("YT_DLP_PROXY", "").strip():
            cmd.extend(["--proxy", os.environ["YT_DLP_PROXY"].strip()])
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
    if not GEMINI_KEY:
        raise ValueError("مفتاح GEMINI_API_KEY غير موجود في GitHub Secrets!")

    client = genai.Client(api_key=GEMINI_KEY)
    
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
    
    models_to_try = [
        'gemini-3.1-pro-preview',
        'gemini-3.1-flash-preview',
        'gemini-3.1-flash-lite-preview',
        'gemini-3-flash-preview'
    ]
    last_err = None
    
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            parsed = validate_gemini_data(parse_gemini_json(response.text))
            print(f"✅ نجح توليد JSON صالح باستخدام نموذج: {model_name}")
            return parsed
        except Exception as e:
            print(f"⚠️ فشلت محاولة النموذج {model_name} ({type(e).__name__}: {str(e)[:180]})، جاري تجربة البديل...")
            last_err = e
            
    raise last_err


# ==========================================
# 5. دالة التحميل والمونتاج إلى ريلز (9:16)
# ==========================================
def download_and_convert_to_reel(url, start_sec, end_sec):
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
        cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--newline",
               "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "25",
               "--merge-output-format", "mp4", "-f",
               "bv*[height<=1080]+ba/b[height<=1080]/b", "-o", "input.%(ext)s"]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
        if os.getenv("YT_DLP_PROXY", "").strip():
            cmd += ["--proxy", os.environ["YT_DLP_PROXY"].strip()]
        # Keep client selection managed by yt-dlp defaults; use PO-token provider only if configured.
        cmd.append(url)
        last_error = ""
        for attempt in range(2):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                candidates = [x for x in Path('.').glob('input.*') if x.suffix.lower() in ('.mp4','.mkv','.webm')]
                if candidates:
                    target = candidates[0]
                    break
            last_error = (result.stderr or result.stdout)[-1400:]
            if os.getenv("YT_DLP_PROXY", "").strip():
                last_error = last_error.replace(os.environ["YT_DLP_PROXY"].strip(), "[PROXY_REDACTED]")
            if any(marker in last_error.lower() for marker in ('sign in to confirm', 'this helps protect our community', 'cookies for the authentication')):
                raise RuntimeError('YOUTUBE_AUTH_REQUIRED: YouTube requires session verification. Provide a permitted source MP4 via SOURCE_VIDEO_FILE or valid account cookies. ' + last_error[-350:])
            if '403' in last_error or 'po token' in last_error.lower():
                raise RuntimeError('YOUTUBE_ACCESS_RESTRICTED: Check PO Token provider / IP restrictions and yt-dlp verbose log. ' + last_error[-600:])
            time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError('VIDEO_DOWNLOAD_FAILED: ' + last_error[-700:])
    probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                            '-of','default=noprint_wrappers=1:nokey=1',str(target)],
                           capture_output=True,text=True,check=True)
    length = float(probe.stdout.strip())
    start, end = float(start_sec), float(end_sec)
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError('Invalid reel interval: non-finite timestamp')
    duration = end - start
    if start < 0 or start >= length or duration < 15 or duration > 59 or end > length + 0.15:
        raise ValueError(f'Invalid reel interval: start={start}, end={end}, source duration={length:.1f}')
    duration = min(duration, length-start)
    if duration < 14.9:
        raise ValueError('Source video is too short for the requested reel interval')
    ffmpeg_cmd = ['ffmpeg','-hide_banner','-y','-ss',str(start),'-i',str(target),'-t',str(duration),
                  '-vf','scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1',
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


def upload_to_youtube(video_path, title, description, tags_str):
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
        clean_tags = [t.strip() for t in tags_str[:490].split(",") if t.strip()]
        
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": clean_tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": os.getenv("YT_PRIVACY_STATUS", "private"),
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        return f"✅ يوتيوب: تم النشر بنجاح! (https://youtu.be/{response['id']})"
    except Exception as e:
        return f"❌ يوتيوب خطأ: {str(e)}"


# ==========================================
# 7. نقطة التشغيل الرئيسية (Main Execution)
# ==========================================
if __name__ == "__main__":
    try:
        if not VIDEO_URL:
            raise ValueError("لم يتم إدخال رابط الفيديو (VIDEO_URL فارغ)!")

        if AGENT_ACTION not in ('seo', 'prepare', 'publish', 'preview'):
            raise ValueError('Unsupported AGENT_ACTION; refusing to publish')
        # Publishing is not activated until the separate final approval stage.
        if AGENT_ACTION == 'publish':
            send_telegram('⛔ النشر متوقف لحين تفعيل الموافقة الصريحة على المعاينة.')
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
            clips_by_id = {item['id']: item for item in plan['clips']}
            send_telegram('بدأ تجهيز معاينة المقاطع: ' + ', '.join(map(str, chosen_ids)))
            fetch_telegram_source()
            preview_cues = None  # One local-source transcription for all chosen clips.
            for clip_id in chosen_ids:
                if plan_reference(load_clip_plan()) != expected_ref:
                    raise RuntimeError('CLIP_PLAN_CHANGED: source replaced during preview')
                clip = clips_by_id[clip_id]
                download_and_convert_to_reel(VIDEO_URL, clip['start_time'], clip['end_time'])
                if preview_cues is None:
                    # Stage 3: trust the audio of the MP4 being cut, never title-only text.
                    preview_cues, preview_source = obtain_timed_transcript(
                        VIDEO_URL, video_path='input.mp4', cookies_file=COOKIES_FILE,
                        prefer_local_audio=True, prefer_word_timestamps=True)
                    if not preview_cues:
                        raise RuntimeError('CAPTIONS_UNAVAILABLE: no timed transcription for the selected source; cannot send an uncaptioned preview')
                    print(f'PREVIEW_TRANSCRIPTION: {preview_source}; CUES: {len(preview_cues)}')
                    # Validate manual edits against the original ASR words first.
                    original_cues = preview_cues
                    approved_cues = apply_approved_edits(original_cues, VIDEO_URL)
                    # Audio-grounded proofread: Gemini -> Deepgram -> AssemblyAI.
                    reviewed_cues = proofread_timed_cues(original_cues)
                    # Manual edits win even if Gemini changed their original words.
                    preview_cues = preserve_approved_word_edits(original_cues, reviewed_cues, approved_cues)
                captioned_preview = burn_captions(
                    'output_reel.mp4', preview_cues, clip['start_time'], clip['end_time'])
                # Reuse input.mp4 in this same runner for any subsequent selection.
                if Path('input.mp4').is_file():
                    os.environ['SOURCE_VIDEO_FILE'] = str(Path('input.mp4').resolve())
                stamp_start = f"{clip['start_time']:.1f}"
                stamp_end = f"{clip['end_time']:.1f}"
                send_telegram_video(captioned_preview,
                                    f"معاينة {clip_id}: {clip['title']}\n{stamp_start}s - {stamp_end}s\nلم يُنشر الفيديو")
                print(f"CLIP_PREVIEW: ID={clip_id}; START={stamp_start}; END={stamp_end}; CAPTIONS_BURNED=YES; DELIVERED=YES")
            record_preview_result(expected_ref, chosen_ids)
            send_telegram('اكتملت المعاينة للمقاطع: ' + ', '.join(map(str, chosen_ids)) +
                          '. لم يُنشر أي فيديو. يمكنك اختيار أرقام أخرى باستخدام /clip.')
            raise SystemExit(0)

        send_telegram(f"⏳ بدأ الوكيل مهمة {AGENT_ACTION} للفيديو:\n{VIDEO_URL}")

        fetch_telegram_source()

        # 1. Try actual YouTube captions first, then audio transcription from the supplied MP4.
        vid_id = extract_video_id(VIDEO_URL)
        local_source = os.getenv("SOURCE_VIDEO_FILE", "").strip()
        timed_cues, timed_source = obtain_timed_transcript(
            VIDEO_URL, video_path=local_source or None, cookies_file=COOKIES_FILE)
        timing_grounded = bool(timed_cues)
        if timing_grounded:
            save_captions(timed_cues)
            transcript = cues_to_text(timed_cues)
            print(f"TIMED_TRANSCRIPT: {timed_source}; CUES: {len(timed_cues)}")
        else:
            transcript = get_transcript(vid_id, VIDEO_URL)
            print("TIMED_TRANSCRIPT: UNAVAILABLE; TITLE_ONLY_FALLBACK: YES")

        # Stage 1: /prepare lists options ONLY. Never create MP4 or generate SEO here.
        if AGENT_ACTION == 'prepare':
            if not timing_grounded:
                send_telegram('⛔ لا يمكن اقتراح قص موثوق بدون تفريغ مؤقت للكلام. أرسل MP4 مع رابطه.')
                raise SystemExit(0)
            duration = source_duration(local_source or None, timed_cues)
            plan = build_clip_plan(VIDEO_URL, TELEGRAM_FILE_ID, timed_cues, duration, GEMINI_KEY)
            save_clip_plan(plan)
            send_telegram(format_clip_plan(plan))
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
