"""Publish only the exact MP4 bytes previously delivered as Telegram preview.

GitHub artifact is validated against approved plan identity and SHA256 manifest.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
import requests


def download_approved_previews(plan, expected_reference, chosen_ids, http=requests):
    token = os.getenv('GH_PAT', '').strip()
    repo = os.getenv('GITHUB_REPOSITORY', '').strip()
    run_id = str(plan.get('preview_run_id', ''))
    if not token or not re.fullmatch(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', repo):
        raise RuntimeError('GITHUB_ARTIFACT_ACCESS_NOT_CONFIGURED')
    if not re.fullmatch(r'[0-9]+', run_id):
        raise RuntimeError('PREVIEW_RUN_INVALID')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    base = f'https://api.github.com/repos/{repo}'
    list_response = http.get(f'{base}/actions/runs/{run_id}/artifacts', headers=headers, timeout=25)
    list_response.raise_for_status()
    results = [a for a in list_response.json().get('artifacts', [])
               if a.get('name') == f'preview-{run_id}' and not a.get('expired')]
    if len(results) != 1:
        raise RuntimeError('PREVIEW_ARTIFACT_UNAVAILABLE: prepare preview again if expired')
    if int(results[0].get('size_in_bytes', 0)) > 300 * 1024 * 1024:
        raise RuntimeError('PREVIEW_ARTIFACT_TOO_LARGE')
    response = http.get(f"{base}/actions/artifacts/{results[0]['id']}/zip",
                        headers=headers, timeout=120)
    response.raise_for_status()
    if len(response.content) > 300 * 1024 * 1024:
        raise RuntimeError('PREVIEW_ARCHIVE_TOO_LARGE')
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    entries = {e.filename: e for e in archive.infolist()}
    if 'manifest.json' not in entries or entries['manifest.json'].file_size > 32_000:
        raise RuntimeError('PREVIEW_MANIFEST_MISSING')
    manifest = json.loads(archive.read('manifest.json'))
    for key, value in [('plan_ref', expected_reference), ('preview_run_id', run_id),
                       ('video_url', plan['video_url']),
                       ('telegram_file_id', plan.get('telegram_file_id', ''))]:
        if manifest.get(key) != value:
            raise RuntimeError(f'PREVIEW_MANIFEST_MISMATCH: {key}')
    if manifest.get('previewed_ids') != plan.get('previewed_ids'):
        raise RuntimeError('PREVIEW_ID_LIST_MISMATCH')
    output = {}
    for clip_id in chosen_ids:
        filename = f'clip-{clip_id}.mp4'
        info = entries.get(filename)
        if not info or not 10_000 <= info.file_size <= 120 * 1024 * 1024:
            raise RuntimeError('APPROVED_PREVIEW_FILE_MISSING_OR_INVALID')
        data = archive.read(filename)
        if hashlib.sha256(data).hexdigest() != manifest.get('files', {}).get(str(clip_id)):
            raise RuntimeError('PREVIEW_SHA256_MISMATCH')
        output[clip_id] = data
    return output


def publish_approved_clips(expected_reference, choice_text, video_url, file_id,
                           read_plan, save_plan, upload, notify, downloader=download_approved_previews):
    plan = read_plan()
    if not expected_reference or plan.get('created_utc') != expected_reference:
        raise RuntimeError('PUBLISH_PLAN_MISMATCH')
    if plan.get('status') != 'publish_queued':
        raise RuntimeError('PUBLISH_NOT_APPROVED: /approve required')
    if plan.get('video_url') != video_url or plan.get('telegram_file_id', '') != file_id:
        raise RuntimeError('PUBLISH_SOURCE_MISMATCH')
    ids = plan.get('approved_ids')
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(type(i) is not int for i in ids):
        raise RuntimeError('PUBLISH_APPROVED_IDS_INVALID')
    if ','.join(map(str, ids)) != choice_text:
        raise RuntimeError('PUBLISH_IDS_MISMATCH')
    if not set(ids).issubset(set(plan.get('previewed_ids', []))):
        raise RuntimeError('PUBLISH_UNPREVIEWED_CLIP')
    if not isinstance(plan.get('approval_utc'), str) or not plan.get('approval_utc'):
        raise RuntimeError('PUBLISH_APPROVAL_MISSING')
    files = downloader(plan, expected_reference, ids)  # validate all before any upload
    if not os.getenv('YT_CLIENT_ID') or not os.getenv('YT_CLIENT_SECRET') or not os.getenv('YT_REFRESH_TOKEN'):
        raise RuntimeError('YOUTUBE_OAUTH_MISSING: refuse to claim publication')
    # Reserve publication state: on a crash, never retry blindly and accidentally duplicate uploads.
    if read_plan().get('status') != 'publish_queued':
        raise RuntimeError('PUBLISH_STATE_CHANGED')
    plan['status'] = 'publishing'
    save_plan(plan)
    completed = []
    try:
        with tempfile.TemporaryDirectory() as temp:
            for clip_id in ids:
                clip = next((c for c in plan['clips'] if c['id'] == clip_id), None)
                if not clip:
                    raise RuntimeError('PUBLISH_CLIP_NOT_IN_PLAN')
                target = Path(temp) / f'clip-{clip_id}.mp4'
                target.write_bytes(files[clip_id])
                title = clip['title'][:100]
                # Do not fabricate SEO or AI copy from title-only info.
                desc = f"مقطع من الفيديو الأصلي: {video_url}"
                result = upload(str(target), title, desc, '')
                if not isinstance(result, str) or not result.startswith('✅'):
                    raise RuntimeError(f'YOUTUBE_UPLOAD_UNCONFIRMED: {result}')
                completed.append({'clip_id': clip_id, 'result': result})
                plan['published_results'] = completed
                save_plan(plan)
                notify(result)
        plan['status'] = 'published'
        save_plan(plan)
        notify('تم نشر المقاطع المعتمدة فقط: ' + ', '.join(map(str, ids)))
    except Exception:
        # Deliberately keep 'publishing' to prevent replays after ambiguous upload failures.
        raise
    return completed
