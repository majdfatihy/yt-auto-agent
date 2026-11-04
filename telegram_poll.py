"""Poll authorized Telegram chat and dispatch each MP4/video request once."""
import json
import os
import re
import requests

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
        'لوحة تحكم الوكيل V5\n'
        '1) أرسل رابط يوتيوب أو MP4 مع رابطه لحفظ المصدر فقط.\n'
        '2) /prepare — تجهيز معاينة ريلز من آخر مصدر، بدون نشر.\n'
        '3) /seo — توليد بيانات وصفية فقط من آخر مصدر.\n'
        '4) /publish — نشر آخر مصدر على يوتيوب بعد أمر صريح منك.\n'
        '5) /status — عرض المصدر المحفوظ.\n'
        '6) /cancel — إلغاء المصدر المحفوظ.\n'
        'يمكنك أيضًا إرسال /prepare أو /seo أو /publish متبوعًا برابط يوتيوب.\n'
        'مهم: /publish أمر نشر مباشر، ولا يُنفّذ بمجرد إرسال الفيديو.'))


def read_source():
    raw, exists = get_variable('TELEGRAM_LAST_SOURCE', '{}')
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}, exists
    except ValueError:
        raise RuntimeError('Invalid TELEGRAM_LAST_SOURCE JSON')


def save_source(data, exists):
    set_variable('TELEGRAM_LAST_SOURCE', json.dumps(data, ensure_ascii=False), exists)


def dispatch(link, media, action, request_message_id):
    payload = {'video_url': link, 'agent_action': action}
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
            telegram('sendMessage', chat_id=CHAT, text='تم إلغاء المصدر المحفوظ. لم يُنشر شيء.')
        elif command == '/status':
            telegram('sendMessage', chat_id=CHAT, text='آخر مصدر محفوظ: ' + (source.get('video_url') or 'لا يوجد'))
        elif command in ('/prepare', '/seo', '/publish'):
            action = {'/prepare':'prepare', '/seo':'seo', '/publish':'publish'}[command]
            selected = {'video_url':link, 'telegram_file_id':media.get('file_id') if media else ''} if link else source
            if not selected.get('video_url'):
                telegram('sendMessage', chat_id=CHAT, text='أرسل رابط فيديو أو MP4 مع رابطه أولًا، ثم أرسل الأمر المطلوب.')
            else:
                payload_media = {'file_id': selected['telegram_file_id']} if selected.get('telegram_file_id') else None
                if dispatch(selected['video_url'], payload_media, action, request_message_id):
                    telegram('sendMessage', chat_id=CHAT, text={
                        'prepare':'تم طلب تجهيز المعاينة فقط. لا يوجد نشر تلقائي.',
                        'seo':'تم طلب البيانات الوصفية فقط. لا يوجد نشر.',
                        'publish':'تم استلام أمر النشر الصريح على يوتيوب. جارٍ التنفيذ.'}[action])
        elif link:
            if media and media.get('file_size', 0) > MAX_FILE:
                telegram('sendMessage', chat_id=CHAT, text='ملف MP4 أكبر من 20MB؛ استخدم ملفًا أصغر أو مصدرًا بديلًا.')
            else:
                save_source({'video_url':link, 'telegram_file_id':media.get('file_id', '') if media else ''}, source_exists)
                telegram('sendMessage', chat_id=CHAT, text='تم حفظ مصدر الفيديو فقط، ولم تبدأ المعالجة أو النشر. أرسل /prepare أو /seo أو /publish.')
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
