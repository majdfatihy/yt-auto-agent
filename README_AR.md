# تحديث استخراج الكابشنز والتفريغ — 2026-11-04

## الملفات
- `agent.py`: استخدام نص مؤقت فعلي أولًا، ومنع القص عند غياب التوقيتات أو عدم تغطيتها للمقطع.
- `transcription_pipeline.py`: سحب كابشنز يوتيوب VTT، أو استخراج MP3 من MP4، ثم المحاولة بالتتابع عبر Groq وDeepgram وAssemblyAI وGemini. حفظ SRT/VTT داخل بيئة التشغيل.
- `main.yml`: تمرير مفاتيح مزودي التفريغ عبر GitHub Secrets.
- `requirements.txt`: لم يتغير؛ المكتبات المطلوبة موجودة بالفعل.

## التركيب
انسخ `agent.py` و`transcription_pipeline.py` و`requirements.txt` إلى جذر المستودع، وانسخ `main.yml` إلى `.github/workflows/main.yml`.
أضف في GitHub Actions Secrets: `GROQ_API_KEY` و`DEEPGRAM_API_KEY` و`ASSEMBLYAI_API_KEY` (يمكن بدء الاختبار بمفتاح واحد). مفتاح `GEMINI_API_KEY` موجود أصلًا.

## الاختبار
أرسل MP4 صغيرًا مع رابط يوتيوب في caption ثم `/prepare` فقط. راجع في Actions: `CAPTIONS: YOUTUBE` أو `TRANSCRIPTION: GROQ/DEEPGRAM/ASSEMBLYAI/GEMINI`، ثم `TIMED_TRANSCRIPT`، وبعدها نتيجة المعاينة في تيليجرام.
لا ترسل `/publish` حتى تراجع المعاينة.

## الحدود
- إذا فشل yt-dlp في الوصول إلى كابشنز يوتيوب، يتم الانتقال للتفريغ الصوتي من MP4.
- لا توجد مقارنة WER/CER آلية في هذه النسخة؛ اختيار المحرك الأول ناجحًا فقط. المقارنة العلمية تتطلب تفريغًا مرجعيًا بشريًا.
- التوقيتات من Gemini توليدية وتحتاج مراجعة بشرية؛ فحص التغطية ليس ضمانًا لدقة نقطة القص.
- ملفا SRT/VTT محفوظان في تشغيل GitHub فقط، ولا يُرسلان إلى تيليجرام تلقائيًا.
- لا يوجد حرق ترجمة على الفيديو في هذه النسخة.
- الحد السحابي لتنزيل MP4 عبر Telegram Bot API يظل 20MB.
- لم تُنفذ اختبارات API حقيقية أو قص/رفع يوتيوب في هذه البيئة.
