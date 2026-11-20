"""Google Sheets integration for the Reels bot (opt-in, no publishing side effects).

Required: GOOGLE_SERVICE_ACCOUNT_JSON (GitHub secret), GOOGLE_SHEET_ID (variable).
The service account must have Editor access to the spreadsheet.
Never log credentials or publish based on a spreadsheet row without explicit approval.
"""
import json
import os
import re
from urllib.parse import urlparse, parse_qs

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DATABASE = "Video_Database"
REGISTRY = "Source_Registry"
OUTPUT = "Reels_Output"
SETTINGS = "Bot_Settings"


def client():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if not raw or not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_NOT_CONFIGURED")
    try:
        info = json.loads(raw)
        if info.get("type") != "service_account":
            raise ValueError("Expected service account credentials")
        credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_INVALID") from exc
    return build("sheets", "v4", credentials=credentials, cache_discovery=False), sheet_id


def _values(tab, cell_range):
    service, spreadsheet_id = client()
    return service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab}!{cell_range}"
    ).execute().get("values", [])


def _append(tab, row):
    service, spreadsheet_id = client()
    return service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=f"{tab}!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[str(v) if v is not None else "" for v in row]]},
    ).execute()


def youtube_id(url):
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    ident = ""
    if host in ("youtu.be", "www.youtu.be"):
        ident = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            ident = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.match(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})", parsed.path)
            ident = match.group(1) if match else ""
    return ident if re.fullmatch(r"[A-Za-z0-9_-]{11}", ident) else None


def list_database():
    """Read the user-managed video catalog. Never process entries implicitly."""
    rows = _values(DATABASE, "A1:I3000")
    if not rows:
        return []
    keys = rows[0]
    return [dict(zip(keys, row)) for row in rows[1:] if any(str(x).strip() for x in row)]


def source_exists(video_url):
    ident = youtube_id(video_url)
    if not ident:
        raise ValueError("YOUTUBE_SOURCE_ID_INVALID")
    rows = _values(REGISTRY, "A2:B10000")
    return any(row and row[0] == ident for row in rows)


def register_source(video_url, title, input_source="telegram"):
    """Register only new YouTube sources. Call serially; not an atomic lock."""
    ident = youtube_id(video_url)
    if not ident:
        raise ValueError("YOUTUBE_SOURCE_ID_INVALID")
    if source_exists(video_url):
        return {"created": False, "video_id": ident}
    if input_source not in ("telegram", "sheets"):
        raise ValueError("INPUT_SOURCE_INVALID")
    _append(REGISTRY, [ident, f"https://www.youtube.com/watch?v={ident}",
                       title, 0, "pending", "", "", input_source, ""])
    return {"created": True, "video_id": ident}


def record_reel_result(reel_id, source_video_id, reel_number, title,
                       start_seconds, end_seconds, destination, account_id,
                       status, published_url="", post_id="", note="",
                       scheduled_at="", published_at="", approved_at=""):
    """Append an audit record; never imply success without a confirmed platform response."""
    allowed = {"pending", "approved", "scheduled", "uploading",
               "published", "failed", "needs_review"}
    if status not in allowed:
        raise ValueError("PUBLISH_STATUS_INVALID")
    if not reel_id or not source_video_id or not destination:
        raise ValueError("REEL_RECORD_REQUIRED_FIELD_MISSING")
    start, end = float(start_seconds), float(end_seconds)
    if start < 0 or end <= start:
        raise ValueError("REEL_TIME_RANGE_INVALID")
    _append(OUTPUT, [reel_id, source_video_id, reel_number, title,
                     start, end, end - start, destination, account_id,
                     status, scheduled_at, published_at, published_url,
                     post_id, note, approved_at])


def healthcheck():
    """Read-only connection check, safe for workflow smoke tests."""
    service, spreadsheet_id = client()
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="spreadsheetId,sheets.properties.title"
    ).execute()
    names = {item["properties"]["title"] for item in metadata.get("sheets", [])}
    missing = {DATABASE, REGISTRY, OUTPUT, SETTINGS} - names
    if missing:
        raise RuntimeError("GOOGLE_SHEETS_MISSING_TABS: " + ",".join(sorted(missing)))
    return {"connected": True, "tabs": sorted(names)}
