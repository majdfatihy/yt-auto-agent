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
        link = get_link(msg)
        media = get_mp4(msg)
        original = msg.get('reply_to_message') or {}
        if not media and link and get_mp4(original):
            media = get_mp4(original)
            request_message_id = original['message_id']
        else:
            request_message_id = msg.get('message_id')
        if media and not link:
            telegram('sendMessage', chat_id=CHAT,
                     text='وصل ملف MP4. أضف رابط يوتيوب في تعليق الملف باستخدام تعديل، أو رد على الملف بالرابط.')
            offset = next_offset
            continue
        if not link:
            # Ignore commands and irrelevant messages without dispatching.
            offset = next_offset
            continue
        key = f'{CHAT}:{request_message_id}'
        if key in seen:
            print('Duplicate message skipped:', request_message_id)
            offset = next_offset
            continue
        payload = {'video_url': link}
        if media:
            size = media.get('file_size', 0)
            if size > MAX_FILE:
                telegram('sendMessage', chat_id=CHAT, text='ملف MP4 أكبر من حد تنزيل Telegram السحابي (20MB).')
                offset = next_offset
                continue
            payload['telegram_file_id'] = media['file_id']
            payload['telegram_message_id'] = request_message_id
            print('Dispatching MP4 message:', request_message_id, 'size:', size)
        else:
            print('Dispatching YouTube link message:', request_message_id)
        r = requests.post(f'{API}/dispatches', headers=HEADERS,
                          json={'event_type': 'process_video', 'client_payload': payload}, timeout=25)
        if r.status_code != 204:
            raise RuntimeError(f'GitHub dispatch HTTP {r.status_code}: {r.text[:250]}')
        seen.add(key)
        processed.append(key)
        # Persist dedup before advancing offset to reduce repeat dispatch on later failures.
        set_variable('TELEGRAM_PROCESSED_MESSAGES', json.dumps(processed[-150:]), processed_exists)
        processed_exists = True
        telegram('sendMessage', chat_id=CHAT,
                 text=('✅ تم إرسال ملف MP4 إلى المعالجة.\n' if media else '✅ تم إرسال رابط يوتيوب إلى المعالجة.\n') + link)
        offset = next_offset
        set_variable('TELEGRAM_UPDATE_OFFSET', offset, offset_exists)
        offset_exists = True
    set_variable('TELEGRAM_UPDATE_OFFSET', offset, offset_exists)
    print('Telegram polling complete; next offset:', offset)

if __name__ == '__main__':
    main()
