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


def store_clip_transcripts(plan, clip_texts, timed_cues=None, http=requests):
    raw = json.dumps({"plan_ref":plan["created_utc"], "texts":clip_texts,
                      "cues": timed_cues or []},
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


def load_clip_transcripts(plan, http=requests):
    count = plan.get("transcript_chunks", 0)
    if type(count) is not int or not 1 <= count <= MAX_CHUNKS:
        raise RuntimeError("REEL_TRANSCRIPT_MISSING")
    token = os.getenv("GH_PAT","").strip()
    repo = os.getenv("GITHUB_REPOSITORY","").strip()
    if not token or not repo:
        raise RuntimeError("REEL_TRANSCRIPT_AUTH_MISSING")
    headers = {"Authorization":f"Bearer {token}", "Accept":"application/vnd.github+json",
               "X-GitHub-Api-Version":"2022-11-28"}
    chunks=[]
    for i in range(count):
        response=http.get(
            f"https://api.github.com/repos/{repo}/actions/variables/{ROOT}{i}",
            headers=headers,timeout=25)
        response.raise_for_status()
        chunks.append(response.json()["value"])
    raw = gzip.decompress(base64.b64decode("".join(chunks), validate=True))
    if hashlib.sha256(raw).hexdigest() != plan.get("transcript_sha256"):
        raise RuntimeError("REEL_TRANSCRIPT_CHANGED")
    payload=json.loads(raw)
    if payload.get("plan_ref") != plan.get("created_utc"):
        raise RuntimeError("REEL_TRANSCRIPT_PLAN_MISMATCH")
    return payload


CAPTION_ROOT = "TELEGRAM_REEL_CAPTION_"


def store_caption_cues(plan, cues, http=requests):
    """Persist the exact timed captions selected for the final render.

    Stored separately from source transcription/SEO so editing caption text
    never silently changes the original topic analysis.
    """
    if not isinstance(cues, list) or not cues:
        raise RuntimeError("REEL_CAPTION_CUES_EMPTY")
    payload = {"plan_ref": plan["created_utc"], "cues": cues}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = base64.b64encode(gzip.compress(raw, compresslevel=9)).decode("ascii")
    chunks = [compressed[i:i + CHUNK_SIZE]
              for i in range(0, len(compressed), CHUNK_SIZE)]
    if not chunks or len(chunks) > MAX_CHUNKS:
        raise RuntimeError("REEL_CAPTIONS_TOO_LARGE")
    token = os.getenv("GH_PAT", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        raise RuntimeError("REEL_CAPTIONS_AUTH_MISSING")
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    for index, chunk in enumerate(chunks):
        name = f"{CAPTION_ROOT}{index}"
        old = http.get(f"{base}/{name}", headers=headers, timeout=25)
        if old.status_code == 404:
            result = http.post(base, headers=headers,
                               json={"name": name, "value": chunk}, timeout=25)
            expected = 201
        else:
            old.raise_for_status()
            result = http.patch(f"{base}/{name}", headers=headers,
                                json={"value": chunk}, timeout=25)
            expected = 204
        if result.status_code != expected:
            raise RuntimeError(f"REEL_CAPTIONS_STORAGE_FAILED: HTTP {result.status_code}")
    plan["caption_cues_chunks"] = len(chunks)
    plan["caption_cues_sha256"] = hashlib.sha256(raw).hexdigest()
    return plan


def load_caption_cues(plan, http=requests):
    """Return None for legacy plans; reject corrupted/stale stored cue data."""
    count = plan.get("caption_cues_chunks")
    if count is None:
        return None
    if type(count) is not int or not 1 <= count <= MAX_CHUNKS:
        raise RuntimeError("REEL_CAPTIONS_COUNT_INVALID")
    token = os.getenv("GH_PAT", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        raise RuntimeError("REEL_CAPTIONS_AUTH_MISSING")
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    chunks = []
    for index in range(count):
        response = http.get(f"{base}/{CAPTION_ROOT}{index}",
                            headers=headers, timeout=25)
        response.raise_for_status()
        chunks.append(response.json()["value"])
    try:
        raw = gzip.decompress(base64.b64decode("".join(chunks), validate=True))
        if hashlib.sha256(raw).hexdigest() != plan.get("caption_cues_sha256"):
            raise RuntimeError("REEL_CAPTIONS_DIGEST_MISMATCH")
        payload = json.loads(raw)
    except (ValueError, OSError) as exc:
        raise RuntimeError("REEL_CAPTIONS_CORRUPTED") from exc
    if payload.get("plan_ref") != plan.get("created_utc"):
        raise RuntimeError("REEL_CAPTIONS_PLAN_MISMATCH")
    if not isinstance(payload.get("cues"), list) or not payload["cues"]:
        raise RuntimeError("REEL_CAPTIONS_EMPTY")
    return payload["cues"]
