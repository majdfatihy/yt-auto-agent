import os
import json
import subprocess
import requests
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 1. استدعاء المفاتيح السرية من GitHub Secrets
# ==========================================
VIDEO_URL = os.getenv("VIDEO_URL")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# مفاتيح فيسبوك (اختيارية: يعمل الكود بدونها ويرسل لك النتائج على تيليجرام)
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_TOKEN = os.getenv("FB_ACCESS_TOKEN")

# مفاتيح يوتيوب (اختيارية: لرفع الفيديو تلقائياً على قناتك)
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")


# ==========================================
# 2. دوال الاتصال واستخراج البيانات
# ==========================================
def send_telegram(msg):
    """إرسال رسالة نصية إلى هاتفك عبر تيليجرام"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram credentials missing. Message:", msg)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # تقسيم الرسالة إذا كانت أطول من حد تيليجرام (4096 حرف)
    for i in range(0, len(msg), 4000):
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg[i:i+4000]})


def send_telegram_video(video_path, caption=""):
    """إرسال فيديو الريلز النهائي إلى تيليجرام لتتمكن من حفظه أو نشره يدوياً أيضاً"""
    if not TG_TOKEN or not TG_CHAT_ID or not os.path.exists(video_path):
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo"
    with open(video_path, 'rb') as video_file:
        requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "caption": caption[:1000]},
            files={"video": video_file}
        )


def extract_video_id(url):
    """استخراج معرف الفيديو (Video ID) من أي صيغة لرابط يوتيوب"""
    parsed = urlparse(url)
    if parsed.hostname == 'youtu.be':
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query)['v'][0]
        if parsed.path.startswith('/shorts/') or parsed.path.startswith('/live/'):
            return parsed.path.split('/')[2]
    raise ValueError("رابط يوتيوب غير صالح أو غير مدعوم.")


def get_transcript(video_id):
    """سحب الكابشن/الترجمة بالتوقيت الزمني بالثواني"""
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['ar', 'en'])
        formatted = ""
        for item in transcript:
            start = int(item['start'])
            formatted += f"[{start}s] {item['text']}\n"
        return formatted[:20000]  # حد آمن للنص المرسل
    except Exception as e:
        return f"لم يتم العثور على ترجمة نصية مباشرة (اعتمد على سياق الرابط): {str(e)}"


# ==========================================
# 3. دالة الذكاء الاصطناعي وتوليد السيو (Gemini)
# ==========================================
def analyze_with_gemini(transcript_text, video_url):
    """تحليل النص وتوليد جميع عناصر السيو ومنشورات Quora في خانات JSON منفصلة"""
    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = f"""
    أنت خبير سيو يوتيوب ومونتاج. بناءً على نص الفيديو التالي بالتوقيت:
    {transcript_text}
    
    المطلوب إخراج ملف JSON صالح فقط يحتوي على المفاتيح التالية بدقة تامة:
    1. "start_time": ثانية البداية لأقوى مقطع يصلح كفيديو ريلز قصير (رقم صحيح Integer).
    2. "end_time": ثانية النهاية للمقطع بحيث تكون المدة بين 30 إلى 55 ثانية (رقم صحيح Integer).
    3. "long_titles": مصفوفة نصية (Array of 3 strings) تحتوي على 3 عناوين طويلة قوية للسيو.
    4. "short_titles": مصفوفة نصية (Array of 3 strings) تحتوي على 3 عناوين قصيرة جذابة للريلز والشورتس.
    5. "description_block": نص واحد يتكون من 3 أسطر فقط تحتوي على عبارات بحثية مصاغة في جمل مدمجة مع الوصف، يليه في نفس البلوك هاشتاجات عامة ومتخصصة (بدون فواصل زمنية/Timestamps نهائياً).
    6. "tags_string": كلمات مفتاحية (جمل طويلة وقصيرة للمحتوى) مفصولة بفاصلة واحدة (,)، بشرط ألا يتجاوز إجمالي عدد الحروف 500 حرف نهائياً.
    7. "quora_ar": منشور احترافي لمنصة Quora باللغة العربية يلتزم بسياسة النشر، مدمج بداخل نص المنشور رابط الفيديو ({video_url})، ويحتوي على هاشتاج واحد فقط.
    8. "quora_en": منشور احترافي لمنصة Quora باللغة الإنجليزية يلتزم بسياسة النشر، مدمج بداخل نص المنشور رابط الفيديو ({video_url})، ويحتوي على هاشتاج واحد فقط.
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)


# ==========================================
# 4. دالة التحميل والمونتاج إلى ريلز (9:16)
# ==========================================
def download_and_convert_to_reel(url, start_sec, end_sec):
    """تحميل الفيديو وقص أفضل لقطة وتحويلها لأبعاد الهاتف الطولية 1080x1920"""
    # 1. تحميل الفيديو بصيغة MP4
    subprocess.run([
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", "input.mp4",
        url
    ], check=True)
    
    # التأكد من سلامة التوقيت (بين 15 و 58 ثانية)
    duration = int(end_sec) - int(start_sec)
    if duration <= 10 or duration > 59:
        start_sec = 0
        duration = 45

    # 2. قص الفيديو وتحويله إلى 9:16 عبر FFmpeg
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


# ==========================================
# 5. دوال النشر التلقائي (فيسبوك + يوتيوب)
# ==========================================
def upload_to_facebook(video_path, title, description):
    """رفع الفيديو على صفحة فيسبوك عبر Graph API"""
    if not FB_PAGE_ID or not FB_TOKEN:
        return "⏭️ فيسبوك: لم يتم ضبط المفاتيح (تم التخطي)."
    
    try:
        url = f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos"
        caption = f"{title}\n\n{description}"
        with open(video_path, 'rb') as f:
            files = {'source': f}
            data = {'title': title, 'description': caption, 'access_token': FB_TOKEN}
            r = requests.post(url, files=files, data=data)
        if r.status_code == 200:
            return "✅ فيسبوك: تم النشر بنجاح!"
        return f"⚠️ فيسبوك: فشل الرفع ({r.text})"
    except Exception as e:
        return f"❌ فيسبوك خطأ: {str(e)}"


def upload_to_youtube(video_path, title, description, tags_str):
    """رفع الفيديو كـ YouTube Shorts مع العنوان والوصف والتاجز"""
    if not YT_CLIENT_ID or not YT_CLIENT_SECRET or not YT_REFRESH_TOKEN:
        return "⏭️ يوتيوب: لم يتم ضبط المفاتيح (تم التخطي)."
    
    try:
        creds = Credentials(
            None,
            refresh_token=YT_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET
        )
        youtube = build("youtube", "v3", credentials=creds)
        
        # تحويل نص التاجز المفصول بفاصلة إلى قائمة مع ضمان عدم تخطي 500 حرف
        clean_tags = [t.strip() for t in tags_str[:490].split(",") if t.strip()]
        
        body = {
            "snippet": {
                "title": title[:100],  # حد يوتيوب للعنوان 100 حرف
                "description": description,
                "tags": clean_tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        return f"✅ يوتيوب: تم النشر بنجاح! (https://youtu.be/{response['id']})"
    except Exception as e:
        return f"❌ يوتيوب خطأ: {str(e)}"


# ==========================================
# 6. نقطة التشغيل الرئيسية (Main Execution)
# ==========================================
if __name__ == "__main__":
    if not VIDEO_URL:
        send_telegram("⚠️ لم يتم تمرير رابط فيديو للوكيل.")
        exit(1)

    try:
        send_telegram(f"⏳ بدأ الوكيل العمل على الفيديو:\n{VIDEO_URL}")
        
        # 1. استخراج المعرف والنص
        vid_id = extract_video_id(VIDEO_URL)
        transcript = get_transcript(vid_id)
        
        # 2. تحليل المحتوى وتوليد السيو عبر Gemini
        data = analyze_with_gemini(transcript, VIDEO_URL)
        
        # ضمان عدم تخطي الكلمات المفتاحية لـ 500 حرف
        tags_500 = data['tags_string'][:495]
        
        # اختيار العنوان الأول للرفع التلقائي
        chosen_title = data['short_titles'][0]
        description_block = data['description_block']
        
        # 3. إرسال حزمة السيو إلى هاتفك في تيليجرام (مقسمة وجاهزة للنسخ)
        seo_msg = (
            f"🎯 **العناوين الطويلة (3):**\n"
            f"1. {data['long_titles'][0]}\n"
            f"2. {data['long_titles'][1]}\n"
            f"3. {data['long_titles'][2]}\n\n"
            f"⚡ **العناوين القصيرة (3):**\n"
            f"1. {data['short_titles'][0]}\n"
            f"2. {data['short_titles'][1]}\n"
            f"3. {data['short_titles'][2]}\n\n"
            f"📝 **الوصف والهاشتاجات (بلوك واحد للنسخ):**\n"
            f"{description_block}\n\n"
            f"🏷️ **الكلمات المفتاحية ({len(tags_500)} حرف):**\n"
            f"{tags_500}"
        )
        send_telegram(seo_msg)
        
        # إرسال منشور Quora العربي في بلوك مستقل
        send_telegram(f"🇪🇬 **منشور Quora (عربي):**\n\n{data['quora_ar']}")
        
        # إرسال منشور Quora الإنجليزي في بلوك مستقل
        send_telegram(f"🇺🇸 **منشور Quora (إنجليزي):**\n\n{data['quora_en']}")
        
        # 4. تحميل الفيديو وقصه كـ ريلز 9:16
        send_telegram(f"🎬 جاري تحميل الفيديو وقص اللقطة من الثانية ({data['start_time']}) إلى ({data['end_time']})...")
        download_and_convert_to_reel(VIDEO_URL, data['start_time'], data['end_time'])
        
        # إرسال الفيديو المقصوص نفسه إلى محادثتك في تيليجرام أيضاً!
        send_telegram_video("output_reel.mp4", caption=f"{chosen_title}\n\n{description_block}")
        
        # 5. الرفع التلقائي على المنصات المربوطة
        fb_result = upload_to_facebook("output_reel.mp4", chosen_title, description_block)
        yt_result = upload_to_youtube("output_reel.mp4", chosen_title, description_block, tags_500)
        
        send_telegram(f"🚀 **ملخص النشر التلقائي:**\n{fb_result}\n{yt_result}")
        
    except Exception as e:
        send_telegram(f"❌ توقف الوكيل بسبب خطأ:\n{str(e)}")
        raise e
