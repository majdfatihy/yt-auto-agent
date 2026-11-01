import os
import re
import glob
import json
import subprocess
import traceback
import requests
from urllib.parse import urlparse, parse_qs
from google import genai

# استدعاء المفاتيح السرية
VIDEO_URL = os.getenv("VIDEO_URL", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()


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
    """محرك ثلاثي المراحل لسحب النص والترجمة وتخطي حظر IP السحابة مجاناً"""
    
    # المرحلة 1: استخدام yt-dlp بمحاكاة تطبيق Android TV لسحب ملف الترجمة
    try:
        cmd = [
            "yt-dlp",
            "--extractor-args", "youtube:player_client=android_vr,tv,web",
            "--write-auto-sub", "--write-sub",
            "--sub-langs", "ar,en",
            "--skip-download",
            "-o", "sub_file",
            url
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        sub_files = glob.glob("sub_file*.vtt")
        if sub_files:
            with open(sub_files[0], "r", encoding="utf-8") as f:
                raw_vtt = f.read()
            # تنظيف ملف الـ VTT من الأكواد المكررة
            clean_text = re.sub(r'<[^>]+>', '', raw_vtt)
            lines = []
            for line in clean_text.splitlines():
                if not line.startswith(("WEBVTT", "Kind:", "Language:")) and "-->" not in line and line.strip():
                    if not lines or lines[-1] != line.strip():
                        lines.append(line.strip())
            if lines:
                print("✅ تم سحب الكابشن بنجاح عبر المرحلة الأولى (Android TV Client)")
                return "\n".join(lines)[:20000]
    except Exception as e:
        print(f"Stage 1 sub failed: {e}")

    # المرحلة 2: استخدام خوادم Invidious البديلة (تتخطى حظر IP يوتيوب تماماً)
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

    # المرحلة 3: خطة الطوارئ عبر NoEmbed (لا تُحظر أبداً من يوتيوب)
    try:
        r = requests.get(f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}", timeout=10).json()
        print("✅ تم سحب العنوان عبر المرحلة الثالثة (NoEmbed)")
        return f"عنوان الفيديو الأصلي: {r.get('title', '')} - القناة: {r.get('author_name', '')}"
    except Exception:
        return f"رابط الفيديو: {url}"


def analyze_with_gemini(transcript_text, video_url):
    """تحليل النص وتوليد جميع عناصر السيو ومنشورات Quora عبر Gemini 3.8 Flash"""
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
    
    # استخدام أحدث نموذج مدعوم مع fallback تلقائي
    models_to_try = ['gemini-3.8-flash', 'gemini-3-flash', 'gemini-2.5-pro']
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


def download_and_convert_to_reel(url, start_sec, end_sec):
    """تحميل الفيديو وقصه وتحويله إلى 9:16 بمحاكاة عميل Android VR لتخطي الحظر"""
    cmd = [
        "yt-dlp",
        "--extractor-args", "youtube:player_client=android_vr,tv,android",
        "-f", "best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "-o", "input.mp4",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"يوتيوب طلب التحقق (Bot Check) لتحميل ملف الفيديو:\n{res.stderr[-300:]}")
    
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
        
        # 4. محاولة تحميل وقص الفيديو
        try:
            send_telegram(f"🎬 جاري تحميل الفيديو وقص اللقطة من الثانية ({data['start_time']}) إلى ({data['end_time']})...")
            download_and_convert_to_reel(VIDEO_URL, data['start_time'], data['end_time'])
            send_telegram_video("output_reel.mp4", caption=f"{chosen_title}\n\n{description_block}")
            send_telegram("✅ تم قص وإرسال فيديو الريلز بنجاح!")
        except Exception as vid_err:
            send_telegram(f"⚠️ تم إرسال السيو بالكامل أعلاه، لكن يوتيوب طلب كوكيز لتحميل ملف الـ MP4:\n{str(vid_err)}")

    except Exception as e:
        err_trace = traceback.format_exc()
        send_telegram(f"❌ حدث خطأ برمجي:\n{str(e)}\n\nالتفاصيل:\n{err_trace[-800:]}")
        exit(1)
