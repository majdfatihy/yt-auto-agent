import os
import re
import glob
import json
import subprocess
import traceback
import time
import shutil
from pathlib import Path
from urllib.parse import unquote
import requests
from urllib.parse import urlparse, parse_qs
from google import genai
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
    os.environ["SOURCE_VIDEO_FILE"] = str(Path("telegram_source.mp4").resolve())


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
        try:
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg[i:i+4000]}, timeout=15)
        except Exception as e:
            print(f"Telegram error: {e}")


def send_telegram_video(video_path, caption=""):
    """إرسال فيديو الريلز المقصوص إلى تيليجرام"""
    if not TG_TOKEN or not TG_CHAT_ID or not os.path.exists(video_path):
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo"
    with open(video_path, 'rb') as video_file:
        requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "caption": caption[:1000]},
            files={"video": video_file},
            timeout=120
        )


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
                print(f"✅ تم سحب بيانات الفيديو الكاملة عبر المرحلة الثانية ({instance})")
                return f"عنوان الفيديو: {title}\nتفاصيل ومحتوى الفيديو:\n{desc}"[:20000]
        except Exception:
            continue

    # المرحلة 3: خطة الطوارئ عبر NoEmbed
    try:
        r = requests.get(f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}", timeout=10).json()
        print("✅ تم سحب العنوان عبر المرحلة الثالثة (NoEmbed)")
        return f"عنوان الفيديو الأصلي: {r.get('title', '')} - القناة: {r.get('author_name', '')}"
    except Exception:
        return f"رابط الفيديو: {url}"


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
            print(f"✅ نجح التوليد باستخدام نموذج: {model_name}")
            return json.loads(response.text)
        except Exception as e:
            print(f"⚠️ النموذج {model_name} غير متاح، جاري تجربة البديل... ({e})")
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
        shutil.copyfile(source, target)
    else:
        cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--newline",
               "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "25",
               "--merge-output-format", "mp4", "-f",
               "bv*[height<=1080]+ba/b[height<=1080]/b", "-o", "input.%(ext)s"]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
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
    start = max(0, int(start_sec))
    duration = int(end_sec) - start
    if duration < 11 or duration > 59 or start >= length:
        raise ValueError(f'Invalid reel interval: start={start}, end={end_sec}, source duration={length:.1f}')
    duration = min(duration, length-start)
    if duration < 10:
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

        send_telegram(f"⏳ بدأ الوكيل العمل على الفيديو:\n{VIDEO_URL}")
        
        fetch_telegram_source()

        # 1. استخراج المعرف والنص
        vid_id = extract_video_id(VIDEO_URL)
        transcript = get_transcript(vid_id, VIDEO_URL)
        
        # 2. تحليل المحتوى وتوليد السيو عبر Gemini 3.1
        source_exists = bool(os.getenv("SOURCE_VIDEO_FILE", "").strip())
        if source_exists:
            # Audio-aware analysis: upload the original MP4 to Gemini when possible.
            # If the service rejects the upload, keep metadata generation but never
            # pretend that title-only metadata contains verified timestamps.
            try:
                client = genai.Client(api_key=GEMINI_KEY)
                uploaded = client.files.upload(file=os.environ["SOURCE_VIDEO_FILE"])
                prompt = "استخرج ملخصا دقيقا ومقاطع ذات توقيت زمني من هذا الفيديو باللغة العربية. اذكر أزمنة البداية والنهاية بالثواني. لا تخمن معلومات غير موجودة."
                for model in ("gemini-3.1-flash-lite-preview", "gemini-2.5-flash-lite"):
                    try:
                        response = client.models.generate_content(model=model, contents=[uploaded, prompt])
                        transcript = (response.text or "")[:20000]
                        break
                    except Exception as model_error:
                        print("Video understanding model unavailable:", type(model_error).__name__)
            except Exception as source_error:
                print("Source-video understanding unavailable:", type(source_error).__name__)
        data = analyze_with_gemini(transcript, VIDEO_URL)
        if not source_exists and transcript.startswith(("عنوان الفيديو الأصلي:", "رابط الفيديو:")):
            send_telegram("⚠️ تم توليد السيو من العنوان فقط؛ توقيت المقطع مقترح وغير موثوق دون تحليل الفيديو.")
        tags_500 = data['tags_string'][:495]
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
        send_telegram(f"🇪🇬 منشور Quora (عربي):\n\n{data['quora_ar']}")
        send_telegram(f"🇺🇸 منشور Quora (إنجليزي):\n\n{data['quora_en']}")
        
        # 4. تحميل وقص الفيديو والنشر التلقائي
        try:
            send_telegram(f"🎬 جاري تحميل الفيديو وقص اللقطة من الثانية ({data['start_time']}) إلى ({data['end_time']})...")
            download_and_convert_to_reel(VIDEO_URL, data['start_time'], data['end_time'])
            send_telegram_video("output_reel.mp4", caption=f"{chosen_title}\n\n{description_block}")
            
            fb_res = upload_to_facebook("output_reel.mp4", chosen_title, description_block)
            yt_res = upload_to_youtube("output_reel.mp4", chosen_title, description_block, tags_500)
            send_telegram(f"🚀 ملخص النشر التلقائي:\n{fb_res}\n{yt_res}")
        except Exception as vid_err:
            send_telegram(f"⚠️ تم إرسال السيو بالكامل أعلاه، لكن حدث عائق أثناء تحميل/قص ملف الـ MP4:\n{str(vid_err)}")
            if "YOUTUBE_AUTH_REQUIRED" in str(vid_err):
                send_telegram("📎 أرسل ملف MP4 إلى البوت مع رابط فيديو يوتيوب في التعليق (caption) لاستكمال المعالجة. الحد الحالي للتنزيل عبر Telegram Bot API السحابي هو 20MB.")
            raise

    except Exception as e:
        err_trace = traceback.format_exc()
        send_telegram(f"❌ حدث خطأ برمجي:\n{str(e)}\n\nالتفاصيل:\n{err_trace[-800:]}")
        exit(1)
