import os
import json
import subprocess
import traceback
import requests
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai

# استدعاء المفاتيح السرية
VIDEO_URL = os.getenv("VIDEO_URL", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()


def send_telegram(msg):
    """إرسال رسالة نصية إلى تيليجرام مع طباعتها في سجل GitHub أيضاً"""
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
    """سحب الكابشن المتاح (يدوي أو تلقائي) مع التوافق مع كافة إصدارات المكتبة"""
    try:
        # متوافق مع الإصدارات الحديثة والقديمة من youtube-transcript-api
        if hasattr(YouTubeTranscriptApi, 'list_transcripts'):
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            try:
                t = transcript_list.find_transcript(['ar', 'en'])
            except Exception:
                t = next(iter(transcript_list))
            data = t.fetch()
        else:
            api = YouTubeTranscriptApi()
            data = api.fetch(video_id, languages=['ar', 'en'])

        formatted = ""
        for item in data:
            start = int(item['start'] if isinstance(item, dict) else item.start)
            text = item['text'] if isinstance(item, dict) else item.text
            formatted += f"[{start}s] {text}\n"
        return formatted[:20000]
    except Exception as e:
        print(f"Transcript fallback triggered: {e}")
        # خطة بديلة: سحب عنوان الفيديو من صفحة يوتيوب نفسها إذا لم تتوفر ترجمة
        try:
            r = requests.get(f"https://noembed.com/embed?url={url}", timeout=10).json()
            return f"عنوان الفيديو الأصلي: {r.get('title', '')} - القناة: {r.get('author_name', '')}"
        except Exception:
            return f"رابط الفيديو: {url}"


def analyze_with_gemini(transcript_text, video_url):
    """تحليل النص وتوليد جميع عناصر السيو ومنشورات Quora عبر Gemini"""
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
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)


def download_and_convert_to_reel(url, start_sec, end_sec):
    """تحميل الفيديو وقصه وتحويله إلى 9:16 مع استخدام عميل أندرويد لتقليل حظر يوتيوب"""
    cmd = [
        "yt-dlp",
        "--extractor-args", "youtube:player_client=android,web",
        "-f", "best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "-o", "input.mp4",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"يوتيوب طلب التحقق لتحميل ملف الفيديو:\n{res.stderr[-300:]}")
    
    duration = int(end_sec) - int(start_sec)
    if duration <= 10 or duration > 59:
        start_sec, duration = 0, 45

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", "input.mp4",
        "-t", str(duration),
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "output_reel.mp4"
    ]
    subprocess.run(ffmpeg_cmd, check=True)


if __name__ == "__main__":
    try:
        if not VIDEO_URL:
            raise ValueError("لم يتم إدخال رابط الفيديو (VIDEO_URL فارغ)!")

        send_telegram(f"⏳ بدأ الوكيل العمل على الفيديو:\n{VIDEO_URL}")
        
        # 1. استخراج المعرف والنص
        vid_id = extract_video_id(VIDEO_URL)
        transcript = get_transcript(vid_id, VIDEO_URL)
        
        # 2. تحليل المحتوى وتوليد السيو عبر Gemini
        data = analyze_with_gemini(transcript, VIDEO_URL)
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
        
        # 4. محاولة تحميل وقص الفيديو (مستقلة حتى لا توقف السيو لو حظر يوتيوب السيرفر)
        try:
            send_telegram(f"🎬 جاري تحميل الفيديو وقص اللقطة من الثانية ({data['start_time']}) إلى ({data['end_time']})...")
            download_and_convert_to_reel(VIDEO_URL, data['start_time'], data['end_time'])
            send_telegram_video("output_reel.mp4", caption=f"{chosen_title}\n\n{description_block}")
            send_telegram("✅ تم قص وإرسال فيديو الريلز بنجاح!")
        except Exception as vid_err:
            send_telegram(f"⚠️ تم توليد السيو بنجاح، لكن تعذر تحميل ملف الفيديو المباشر من يوتيوب بدون كوكيز:\n{str(vid_err)}")

    except Exception as e:
        err_trace = traceback.format_exc()
        send_telegram(f"❌ حدث خطأ برمجي:\n{str(e)}\n\nالتفاصيل:\n{err_trace[-800:]}")
        exit(1)
