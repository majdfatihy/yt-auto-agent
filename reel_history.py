"""Persistent per-source publishing history, independent of the current clip plan.

History is a GitHub Actions repository variable per canonical source ID; it survives
/prepare, /cancel, and a new plan. The older, independently confirmed upload is
seeded separately. Never infer clip windows from a YouTube title or guess dates.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests

# Confirmed historical upload from the original bot run, before history tracking.
# This is NOT a general discovery of every past upload.
CONFIRMED_LEGACY_UPLOADS = {
    "drive:1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O": [
        {"start_time": 66.5, "end_time": 106.4,
         "youtube_video_id": "nY9hYCj2fJ4", "status": "private",
         "source": "confirmed_legacy_run_43779147786"}
    ],
}


def canonical_source(url):
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    if host in ("drive.google.com", "www.drive.google.com"):
        match = re.search(r"/file/d/([A-Za-z0-9_-]{15,})", parsed.path)
        ident = match.group(1) if match else parse_qs(parsed.query).get("id", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{15,}", ident):
            return "drive:" + ident
    if host in ("youtu.be", "www.youtu.be"):
        ident = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[\w-]{11}", ident):
            return "youtube:" + ident
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            ident = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.match(r"/(?:shorts|live|embed)/([\w-]{11})", parsed.path)
            ident = match.group(1) if match else ""
        if re.fullmatch(r"[\w-]{11}", ident):
            return "youtube:" + ident
    if not str(url).strip():
        raise ValueError("REEL_HISTORY_EMPTY_SOURCE")
    return "source:" + str(url).strip()


def variable_name(source_url):
    canonical = canonical_source(source_url)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24].upper()
    return "REEL_PUBLISHED_" + digest


def overlaps(left_start, left_end, right_start, right_end, tolerance=0.25):
    return min(float(left_end), float(right_end)) - max(
        float(left_start), float(right_start)) > tolerance


def published_windows(history):
    ranges = []
    for entry in history:
        if not isinstance(entry, dict) or not entry.get("youtube_video_id"):
            continue
        try:
            a, b = float(entry["start_time"]), float(entry["end_time"])
        except (ValueError, TypeError, KeyError):
            continue
        if 0 <= a < b:
            ranges.append((a, b))
    return sorted(ranges)


def has_overlap(history, start, end):
    return any(overlaps(start, end, left, right)
               for left, right in published_windows(history))


def _auth():
    token, repo = os.getenv("GH_PAT", "").strip(), os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise RuntimeError("REEL_HISTORY_ACCESS_NOT_CONFIGURED: GH_PAT/GITHUB_REPOSITORY required")
    return (
        f"https://api.github.com/repos/{repo}/actions/variables",
        {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    )


def load_history(source_url, http=requests):
    key = canonical_source(source_url)
    values = list(CONFIRMED_LEGACY_UPLOADS.get(key, []))
    endpoint, headers = _auth()
    reply = http.get(endpoint + "/" + variable_name(source_url), headers=headers, timeout=25)
    if reply.status_code == 404:
        return values
    reply.raise_for_status()
    try:
        stored = json.loads(reply.json()["value"])
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("REEL_HISTORY_CORRUPTED: refuse duplicate publication") from error
    if not isinstance(stored, list):
        raise RuntimeError("REEL_HISTORY_NOT_LIST: refuse duplicate publication")
    for entry in stored:
        if isinstance(entry, dict) and entry.get("youtube_video_id") and not any(
                previous.get("youtube_video_id") == entry["youtube_video_id"]
                for previous in values):
            values.append(entry)
    return values


def record_published(source_url, start, end, youtube_id, *,
                     publication_status="private", serial=None, http=requests):
    if not re.fullmatch(r"[\w-]{11}", str(youtube_id)):
        raise RuntimeError("YOUTUBE_ID_UNCONFIRMED: never mark unknown upload published")
    history = load_history(source_url, http=http)
    for item in history:
        if item["youtube_video_id"] == youtube_id:
            return history
    if has_overlap(history, start, end):
        raise RuntimeError("REEL_ALREADY_UPLOADED: duplicate source time range")
    history.append({
        "start_time": round(float(start), 2), "end_time": round(float(end), 2),
        "youtube_video_id": youtube_id, "status": publication_status,
        **({"serial": serial} if type(serial) is int and serial >= 1 else {}),
        "source": "verified_upload",
        "recorded_utc": datetime.now(timezone.utc).isoformat()
    })
    value = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    if len(value.encode("utf-8")) > 48_000:
        raise RuntimeError("REEL_HISTORY_TOO_LARGE: refuse upload before losing history")
    endpoint, headers = _auth()
    target = endpoint + "/" + variable_name(source_url)
    lookup = http.get(target, headers=headers, timeout=25)
    if lookup.status_code == 404:
        response = http.post(endpoint, headers=headers,
                             json={"name": variable_name(source_url), "value": value},
                             timeout=25)
        expected = 201
    else:
        lookup.raise_for_status()
        response = http.patch(target, headers=headers,
                              json={"value": value}, timeout=25)
        expected = 204
    if response.status_code != expected:
        raise RuntimeError("REEL_HISTORY_WRITE_FAILED: upload may be complete; "
                           "block retries, inspect YouTube before trying again")
    return history
