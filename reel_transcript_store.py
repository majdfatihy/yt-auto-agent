"""Store complete per-reel text separately from small GitHub Actions plan.

GitHub repository variables have a bounded size. The transcript is gzipped
and chunked without loss, then accessed only through the existing private
GitHub credentials. The plan records the count and SHA-256.
"""
import base64
import gzip
import hashlib
import json
import os
import requests

ROOT = "TELEGRAM_REEL_TEXT_"
CHUNK_SIZE = 42000
MAX_CHUNKS = 16


def store_clip_transcripts(plan, clip_texts, http=requests):
    raw = json.dumps({"plan_ref":plan["created_utc"], "texts":clip_texts},
                     ensure_ascii=False, separators=(",",":")).encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw, compresslevel=9)).decode("ascii")
    chunks = [packed[i:i+CHUNK_SIZE] for i in range(0,len(packed),CHUNK_SIZE)]
    if not chunks or len(chunks) > MAX_CHUNKS:
        raise RuntimeError("REEL_TEXT_TOO_LARGE_FOR_GITHUB_VARIABLES")
    auth = os.environ.get("GH_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not auth or not repo:
        raise RuntimeError("REEL_TRANSCRIPT_STORAGE_NOT_CONFIGURED")
    headers = {"Authorization":f"Bearer {auth}", "Accept":"application/vnd.github+json",
               "X-GitHub-Api-Version":"2022-11-28"}
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    for i, chunk in enumerate(chunks):
        name = f"{ROOT}{i}"
        old = http.get(f"{base}/{name}", headers=headers, timeout=25)
        if old.status_code == 404:
            result = http.post(base, headers=headers,
                               json={"name":name,"value":chunk}, timeout=25)
            expected = 201
        else:
            old.raise_for_status()
            result = http.patch(f"{base}/{name}", headers=headers,
                                json={"value":chunk}, timeout=25)
            expected = 204
        if result.status_code != expected:
            raise RuntimeError(f"REEL_TEXT_STORAGE_FAILED: {name} HTTP {result.status_code}")
    plan["transcript_chunks"] = len(chunks)
    plan["transcript_sha256"] = hashlib.sha256(raw).hexdigest()
    return plan
