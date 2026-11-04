# إصلاح مسار MP4 من تيليجرام — v4

## الملفات التي تغيرت فقط
- `telegram_poll.py`: دعم `edited_message`، ورابط يوتيوب في تعليق ملف MP4، أو الرد على ملف MP4 برابط، وإرسال `telegram_file_id` إلى GitHub Actions، ومنع تكرار الرسالة عبر متغير `TELEGRAM_PROCESSED_MESSAGES`.
- `.github/workflows/telegram-poll.yml`: إضافة `concurrency` لمنع تداخل تشغيل مهمتي polling.
- `agent.py`: التحقق من ملف Telegram بواسطة FFprobe وتسجيل مصدر الملف قبل المعالجة.

لم يتم تعديل منطق السيو أو الرفع أو القص أو مفاتيح GitHub الحالية. `main.yml` بقي دون تعديل لأنه يمرر `TELEGRAM_FILE_ID` بالفعل.

## رفع الملفات من الهاتف
استبدل الملفين `telegram_poll.py` و`agent.py` في جذر المستودع، وملف `.github/workflows/telegram-poll.yml` في مساره. لا ترفع الملفات إلى مسارات مختلفة.

## الاختبار
1. افتح GitHub Actions > Telegram Inbox > Run workflow بعد إرسال ملف MP4 مع الرابط في التعليق، أو الرد على الملف برابط يوتيوب.
2. افتح `Run python telegram_poll.py`: ينبغي أن يظهر `Dispatching MP4 message` وليس `Dispatching YouTube link message`.
3. افتح المهمة الجديدة `process_video`: ينبغي أن يظهر `SOURCE: TELEGRAM_MP4; FILE_RECEIVED: YES; FILE_VALIDATED: YES; YOUTUBE_DOWNLOAD: SKIPPED`.
4. إذا نجح التحويل والرفع بوضع Private، تحقق من رابط Shorts في تيليجرام.

## قيود مهمة
- حد تنزيل Telegram Bot API السحابي 20MB.
- تعديل تعليق رسالة سبق معالجتها كرابط فقط لا يعيد معالجة الرسالة نفسها بسبب منع التكرار. استخدم رسالة ملف جديدة للاختبار عند الحاجة.
- حماية التكرار قائمة على آخر 150 رسالة؛ ليست قاعدة بيانات دائمة.
- لم يُنفذ اختبار حي على GitHub أو Telegram أو YouTube، ولا يوجد ادعاء بنجاح النشر.
