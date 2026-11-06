تحديث بوت yt-auto-agent — الموافقة والنشر
المسار: /clip 1 → وصول المعاينة → /approve 1 → /publish.
ما ينشر هو نفس ملف المعاينة المخزن بـGitHub Actions، بعد التحقق من SHA256 وربطه بالمصدر والتوقيت.
لا يتم النشر دون موافقة، ولا تتكرر عملية النشر عند تكرار الأمر.
نشر يوتيوب في الإعدادات PRIVATE افتراضياً (حساب مستخدم OAuth مفعل مسبقاً).
يلزم ضبط GH_PAT بصلاحيات Actions:read وVariables:read/write، وGITHUB_PAT في Cloudflare بصلاحيات Actions:read وVariables:write وContents:write لعمليات dispatch.
انسخ الملفات إلى الجذر الصحيح من مستودعك بدل الملفات المناظرة، وانشر worker.js إلى Cloudflare Worker.
لم تُنشر التغييرات على أي خدمة، ولم تُختبر واجهات YouTube/GitHub/Telegram الفعلية. اختبارات وهمية فقط.
