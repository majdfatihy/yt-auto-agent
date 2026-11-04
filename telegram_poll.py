"""Poll authorized Telegram chat and dispatch each MP4/video request once."""
import json
import os
import re
import requests
from clip_planner import parse_clip_choices, plan_reference

TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT = str(os.environ['TELEGRAM_CHAT_ID'])
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '').strip()
if not GH_TOKEN:
    raise RuntimeError('GH_PAT is missing')
REPO = os.environ['GITHUB_REPOSITORY']
API = f'https://api.github.com/repos/{REPO}'
HEADERS = {'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
YT = re.compile(r'https?://(?:www\.|m\.)?(?:youtube\.com/watch\?[^\s]*v=|youtube\.com/(?:shorts|live)/|youtu\.be/)[^\s]+', re.I)
MAX_FILE = 20 * 1024 * 1024

def telegram(method, **kwargs):
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}', json=kwargs, timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get('ok'):
        raise RuntimeError(result.get('description', 'Telegram error'))
    return result['result']

def get_variable(name, default):
    r = requests.get(f'{API}/actions/variables/{name}', headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return default, False
    r.raise_for_status()
    return r.json()['value'], True

def set_variable(name, value, exists):
    method = requests.patch if exists else requests.post
    url = f'{API}/actions/variables' + (f'/{name}' if exists else '')
    payload = {'value': str(value)}
    if not exists:
        payload['name'] = name
    r = method(url, headers=HEADERS, json=payload, timeout=20)
    r.raise_for_status()

def get_link(msg):
    match = YT.search((msg.get('text') or msg.get('caption') or '').strip())
    return match.group(0).rstrip('.,)') if match else None

def get_mp4(msg):
    media = msg.get('document') or msg.get('video')
    if not media:
        return None
    filename = (media.get('file_name') or '').lower()
    mime = (media.get('mime_type') or '').lower()
    if not (filename.endswith('.mp4') or mime == 'video/mp4'):
        return None
    return media

def send_menu():
    telegram('sendMessage', chat_id=CHAT, text=(
        'لوحة تحكم الوكيل — اختيار ومعاينة\n'
        '1) أرسل رابط يوتيوب أو MP4 مع رابطه لحفظ المصدر فقط.\n'
        '2) /prepare — اقتراح مقاطع بعناوين وتوقيتات فقط.\n'
        '3) أرسل 1 أو 1,3 أو /clip 1,3 لمعاينة المقاطع المختارة.\n'
        '4) /seo — توليد بيانات وصفية عند الطلب فقط.\n'
        '5) /publish — متوقف حتى الموافقة الصريحة.\n'
        '6) /status — عرض المصدر المحفوظ.\n'
        '7) /cancel — إلغاء المصدر المحفوظ والاقتراحات.\n'
        'يمكنك أيضًا إرسال /prepare أو /seo أو /publish متبوعًا برابط يوتيوب.\n'
        'مهم: الاختيار ينتج معاينة خاصة فقط، ولا يوجد نشر.'))


def read_source():
    raw, exists = get_variable('TELEGRAM_LAST_SOURCE', '{}')
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}, exists
    except ValueError:
        raise RuntimeError('Invalid TELEGRAM_LAST_SOURCE JSON')


def save_source(data, exists):
    set_variable('TELEGRAM_LAST_SOURCE', json.dumps(data, ensure_ascii=False), exists)


def clear_clip_plan():
    """Invalidate any saved suggestions when the source is cancelled or replaced."""
    _, exists = get_variable('TELEGRAM_CLIP_PROPOSALS', '{}')
    if exists:
        set_variable('TELEGRAM_CLIP_PROPOSALS', '{}', True)


def dispatch(link, media, action, request_message_id, extra_payload=None):
    payload = {'video_url': link, 'agent_action': action}
    if extra_payload:
        payload.update(extra_payload)
    if media:
        size = media.get('file_size', 0)
        if size > MAX_FILE:
            telegram('sendMessage', chat_id=CHAT, text='ملف MP4 يتجاوز حد تنزيل Telegram السحابي (20MB).')
            return False
        payload['telegram_file_id'] = media['file_id']
        payload['telegram_message_id'] = request_message_id
    r = requests.post(f'{API}/dispatches', headers=HEADERS,
                      json={'event_type': 'process_video', 'client_payload': payload}, timeout=25)
    if r.status_code != 204:
        raise RuntimeError(f'GitHub dispatch HTTP {r.status_code}: {r.text[:250]}')
    return True


def main():
    offset_raw, offset_exists = get_variable('TELEGRAM_UPDATE_OFFSET', '0')
    processed_raw, processed_exists = get_variable('TELEGRAM_PROCESSED_MESSAGES', '[]')
    offset = int(offset_raw)
    try:
        processed = list(json.loads(processed_raw))[-150:]
    except (ValueError, TypeError):
        raise RuntimeError('Invalid TELEGRAM_PROCESSED_MESSAGES JSON; refusing to reset deduplication')
    seen = set(processed)
    updates = telegram('getUpdates', offset=offset, limit=100, timeout=0,
                       allowed_updates=['message', 'edited_message'])
    print('Telegram updates received:', len(updates))
    for update in updates:
        next_offset = update['update_id'] + 1
        msg = update.get('edited_message') or update.get('message') or {}
        if str(msg.get('chat', {}).get('id')) != CHAT:
            offset = next_offset
            continue
        text = (msg.get('text') or '').strip()
        command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
        link = get_link(msg)
        media = get_mp4(msg)
        original = msg.get('reply_to_message') or {}
        if not media and link and get_mp4(original):
            media = get_mp4(original)
        request_message_id = msg.get('message_id')
        key = f'{CHAT}:{update["update_id"]}'
        if key in seen:
            offset = next_offset
            continue
        source, source_exists = read_source()
        if command in ('/start', '/help', '/menu'):
            send_menu()
        elif command == '/cancel':
            save_source({}, source_exists)
            clear_clip_plan()
            telegram('sendMessage', chat_id=CHAT, text='تم إلغاء المصدر والاقتراحات المحفوظة. لم يُنشر شيء.')
        elif command == '/status':
            telegram('sendMessage', chat_id=CHAT, text='آخر مصدر محفوظ: ' + (source.get('video_url') or 'لا يوجد'))
        elif command == '/clip' or re.fullmatch(r'[0-9٠-٩۰-۹]+(?:[\s,،.]+[0-9٠-٩۰-۹]+)*', text):
            choice_text = text[len(text.split()[0]):].strip() if command == '/clip' else text
            raw, _ = get_variable('TELEGRAM_CLIP_PROPOSALS', '{}')
            try:
                plan = json.loads(raw)
                if not isinstance(plan, dict) or not plan.get('clips'):
                    raise ValueError('لا توجد اقتراحات محفوظة. أرسل /prepare أولًا.')
                if (source.get('video_url') != plan.get('video_url') or
                        source.get('telegram_file_id', '') != plan.get('telegram_file_id', '')):
                    raise ValueError('الاقتراحات لا تتطابق مع المصدر الحالي. أرسل /prepare مرة أخرى.')
                chosen_ids = parse_clip_choices(choice_text, plan)
                reference = plan_reference(plan)
            except (ValueError, TypeError) as error:
                telegram('sendMessage', chat_id=CHAT, text=str(error))
            else:
                selected_file = {'file_id': plan['telegram_file_id']} if plan.get('telegram_file_id') else None
                dispatched = dispatch(plan['video_url'], selected_file, 'preview', request_message_id,
                                      {'clip_ids': ','.join(map(str, chosen_ids)), 'clip_plan_ref': reference})
                if dispatched:
                    telegram('sendMessage', chat_id=CHAT,
                             text='تم طلب معاينة المقاطع: ' + ', '.join(map(str, chosen_ids)) +
                                  '. سيصل كل فيديو منفصلًا، ولن يُنشر على يوتيوب.')
        elif command == '/publish':
            telegram('sendMessage', chat_id=CHAT, text='⛔ النشر متوقف مؤقتًا. لازم تختار المقطع وتراجع المعاينة وتوافق قبل النشر.')
        elif command in ('/prepare', '/seo'):
            action = {'/prepare':'prepare', '/seo':'seo'}[command]
            selected = {'video_url':link, 'telegram_file_id':media.get('file_id') if media else ''} if link else source
            if action == 'prepare' and selected.get('video_url'):
                save_source(selected, source_exists)
                clear_clip_plan()
            if not selected.get('video_url'):
                telegram('sendMessage', chat_id=CHAT, text='أرسل رابط فيديو أو MP4 مع رابطه أولًا، ثم أرسل الأمر المطلوب.')
            else:
                payload_media = {'file_id': selected['telegram_file_id']} if selected.get('telegram_file_id') else None
                if dispatch(selected['video_url'], payload_media, action, request_message_id):
                    telegram('sendMessage', chat_id=CHAT, text={
                        'prepare':'تم طلب تحليل الفيديو واقتراح توقيتات فقط، بدون قص أو نشر.',
                        'seo':'تم طلب البيانات الوصفية فقط. لا يوجد نشر.',
                        'publish':'النشر متوقف حتى المرحلة النهائية.'}[action])
        elif link:
            if media and media.get('file_size', 0) > MAX_FILE:
                telegram('sendMessage', chat_id=CHAT, text='ملف MP4 أكبر من 20MB؛ استخدم ملفًا أصغر أو مصدرًا بديلًا.')
            else:
                save_source({'video_url':link, 'telegram_file_id':media.get('file_id', '') if media else ''}, source_exists)
                clear_clip_plan()
                telegram('sendMessage', chat_id=CHAT, text='تم حفظ المصدر فقط. أرسل /prepare لاقتراح توقيتات أو /seo للبيانات الوصفية.')
        elif media:
            telegram('sendMessage', chat_id=CHAT, text='وصل MP4 بدون رابط. أضف رابط يوتيوب في تعليق الملف أو رد عليه بالرابط؛ لن يبدأ أي نشر تلقائي.')
        elif text:
            telegram('sendMessage', chat_id=CHAT, text='الأمر غير معروف. أرسل /menu لعرض الأوامر.')
        seen.add(key)
        processed.append(key)
        set_variable('TELEGRAM_PROCESSED_MESSAGES', json.dumps(processed[-150:]), processed_exists)
        processed_exists = True
        offset = next_offset
        set_variable('TELEGRAM_UPDATE_OFFSET', offset, offset_exists)
        offset_exists = True
    set_variable('TELEGRAM_UPDATE_OFFSET', offset, offset_exists)
    print('Telegram polling complete; next offset:', offset)

if __name__ == '__main__':
    main()
