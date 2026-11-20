"""Pure Telegram queue dashboard markup; no Telegram API or publishing side effects.

Callers must validate chat ownership before using callback data.
"""
from collections import Counter

PAGE_SIZE = 5
ACTIONS = {"queue:list", "queue:pending", "queue:scheduled", "queue:failed", "queue:refresh"}
LABELS = {"queue:list":"كل المهام", "queue:pending":"تحتاج موافقة",
          "queue:scheduled":"مجدولة", "queue:failed":"مشكلات",
          "queue:refresh":"تحديث"}
ICONS = {"awaiting_approval":"⏳", "scheduled":"🕒", "planned":"✅",
         "already_final":"☑️", "manual_review":"⚠️", "invalid":"❌"}


def dashboard(items, filter_name="queue:list", page=0):
    """Render a bounded, read-only Telegram menu from plan_content_queue()."""
    if filter_name not in ACTIONS:
        raise ValueError("QUEUE_FILTER_INVALID")
    if not isinstance(page, int) or page < 0:
        raise ValueError("QUEUE_PAGE_INVALID")
    states = Counter(x.get("state", "invalid") for x in items)
    filtered = list(items)
    if filter_name == "queue:pending":
        filtered = [x for x in items if x.get("state") == "awaiting_approval"]
    elif filter_name == "queue:scheduled":
        filtered = [x for x in items if x.get("state") == "scheduled"]
    elif filter_name == "queue:failed":
        filtered = [x for x in items if x.get("state") in ("invalid", "manual_review")]
    start = page * PAGE_SIZE
    shown = filtered[start:start + PAGE_SIZE]
    lines = [
        "📋 لوحة إدارة النشر — وضع المعاينة",
        f"الإجمالي: {len(items)} | موافقة: {states['awaiting_approval']} | مجدول: {states['scheduled']}",
        f"جاهز للمراجعة: {states['planned']} | يحتاج تدخل: {states['invalid'] + states['manual_review']}",
        f"عرض {start+1 if shown else 0}–{start+len(shown)} من {len(filtered)}",
    ]
    for item in shown:
        name = str(item.get("task_id") or "بدون رقم").replace("\n", " ")[:45]
        state = item.get("state", "invalid")
        lines.append(f"{ICONS.get(state,'•')} {name} — {state}")
    rows = [
        [{"text": LABELS[key], "callback_data": key} for key in ("queue:list","queue:pending")],
        [{"text": LABELS[key], "callback_data": key} for key in ("queue:scheduled","queue:failed")],
    ]
    nav = []
    if page > 0:
        nav.append({"text":"السابق", "callback_data":f"{filter_name}:{page-1}"})
    if start + PAGE_SIZE < len(filtered):
        nav.append({"text":"التالي", "callback_data":f"{filter_name}:{page+1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text":"تحديث", "callback_data":"queue:refresh"}])
    return {"text":"\n".join(lines), "reply_markup":{"inline_keyboard":rows}}


def parse_callback(value):
    """Only accept known queue navigation callbacks; never authorize publication."""
    if value in ACTIONS:
        return value, 0
    parts = str(value).rsplit(":",1)
    if len(parts) != 2 or parts[0] not in ACTIONS or not parts[1].isdigit():
        raise ValueError("QUEUE_CALLBACK_INVALID")
    page = int(parts[1])
    if page > 100000:
        raise ValueError("QUEUE_PAGE_INVALID")
    return parts[0], page
