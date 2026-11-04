"""Scheduled Telegram polling -> GitHub repository_dispatch. Only authorized chat is accepted."""
import os
import re
import requests

TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT = str(os.environ['TELEGRAM_CHAT_ID'])
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '').strip()
if not GH_TOKEN:
    raise RuntimeError('GH_PAT secret is missing. Configure a fine-grained token with Variables: Read and write, Contents: Read and write.')
REPO = os.environ['GITHUB_REPOSITORY']
API = f'https://api.github.com/repos/{REPO}'
HEADERS = {'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
YT = re.compile(r'https?://(?:www\.|m\.)?(?:youtube\.com/watch\?[^\s]*v=|youtube\.com/(?:shorts|live)/|youtu\.be/)[^\s]+', re.I)

def telegram(method, **kwargs):
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}', json=kwargs, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('description', 'Telegram API failed'))
    return data['result']

def main():
    v = requests.get(f'{API}/actions/variables/TELEGRAM_UPDATE_OFFSET', headers=HEADERS, timeout=20)
    if v.status_code == 404:
        offset = 0
        create = True
    else:
        if v.status_code == 403:
            raise RuntimeError('GitHub HTTP 403 reading Actions variable. Check GH_PAT repository access and Variables: Read and write permission.')
        v.raise_for_status()
        offset = int(v.json()['value'])
        create = False
    updates = telegram('getUpdates', offset=offset, limit=30, timeout=0, allowed_updates=['message'])
    for update in updates:
        offset = update['update_id'] + 1
        msg = update.get('message') or {}
        if str(msg.get('chat', {}).get('id')) != CHAT:
            continue
        content = (msg.get('text') or msg.get('caption') or '').strip()
        link = YT.search(content)
        doc = msg.get('video') or msg.get('document')
        if not link:
            telegram('sendMessage', chat_id=CHAT, text='أرسل رابط يوتيوب، أو ملف MP4 مع رابط يوتيوب في التعليق على الملف.')
            continue
        if doc:
            mime = doc.get('mime_type', '')
            filename = doc.get('file_name', '')
            if mime != 'video/mp4' and not filename.lower().endswith('.mp4'):
                telegram('sendMessage', chat_id=CHAT, text='الملف المطلوب MP4 فقط.')
                continue
            if doc.get('file_size', 0) > 20 * 1024 * 1024:
                telegram('sendMessage', chat_id=CHAT, text='الملف أكبر من 20MB؛ لا يمكن تنزيله من Telegram Bot API السحابي. استخدم ملفا أصغر حاليا.')
                continue
        payload = {'video_url': link.group(0)}
        if doc:
            payload['telegram_file_id'] = doc['file_id']
        r = requests.post(f'{API}/dispatches', headers=HEADERS, json={'event_type': 'process_video', 'client_payload': payload}, timeout=25)
        if r.status_code != 204:
            print('Dispatch failed:', r.status_code, r.text[:300])
            # Do not advance past an unqueued message.
            offset = update['update_id']
            break
        telegram('sendMessage', chat_id=CHAT, text='✅ تم استلام الطلب وإرساله إلى GitHub Actions.\n' + payload['video_url'])
    data = {'name': 'TELEGRAM_UPDATE_OFFSET', 'value': str(offset)} if create else {'value': str(offset)}
    method = requests.post if create else requests.patch
    r = method(f'{API}/actions/variables' + ('' if create else '/TELEGRAM_UPDATE_OFFSET'), headers=HEADERS, json=data, timeout=20)
    if r.status_code == 403:
        raise RuntimeError('GitHub HTTP 403 saving Actions variable. Check GH_PAT Variables: Read and write permission.')
    r.raise_for_status()
    print('Telegram polling complete; next offset:', offset)

if __name__ == '__main__':
    main()
