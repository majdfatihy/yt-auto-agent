# YouTube Reels Agent v2 – اختبار

1. ارفع `agent.py` و`requirements.txt` إلى جذر المستودع، و`main.yml` إلى `.github/workflows/main.yml`.
2. تأكد من GitHub Secrets الحالية؛ لا تنشر الكوكيز أو OAuth tokens في السجلات.
3. نفذ workflow_dispatch برابط فيديو تملك حق استخدامه.
4. راقب خطوة تثبيت Deno وyt-dlp ثم تنزيل الفيديو ثم FFmpeg ثم رفع يوتيوب بوضع Private.
5. إذا ظهر `YOUTUBE_AUTH_REQUIRED` أو `YOUTUBE_ACCESS_RESTRICTED`، أرسل السجل بعد حذف أي بيانات حساسة. لا توجد ضمانات لتجاوز قيود يوتيوب عبر GitHub Actions.

ملاحظات: `SOURCE_VIDEO_FILE` يتطلب وجود ملف مسبقًا داخل بيئة التشغيل؛ استقبال ملفات تيليجرام ليس منفذًا. تم فحص صياغة Python وYAML محليًا فقط، وليس إجراء رفع حقيقي.

المراجع الرسمية:
https://github.com/yt-dlp/yt-dlp/wiki/EJS
https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
https://github.com/yt-dlp/yt-dlp/wiki/FAQ
