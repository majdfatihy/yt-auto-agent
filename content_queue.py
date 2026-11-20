"""Sheet-driven multi-platform content planner. No network posting side effects.

Content_Queue columns (in order):
task_id, content_type, text, link_url, image_url, video_url, destinations,
scheduled_at, timezone, repeat_rule, visibility, approval, status, attempts,
last_error, published_urls, last_run_at

A queue entry is only eligible for execution after explicit approval.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import re

COLUMNS = ("task_id","content_type","text","link_url","image_url","video_url",
           "destinations","scheduled_at","timezone","repeat_rule","visibility",
           "approval","status","attempts","last_error","published_urls","last_run_at")
DESTINATIONS = frozenset({"youtube","facebook","instagram","threads"})
TYPES = frozenset({"video","text","image","link"})
TERMINAL = frozenset({"published","cancelled"})
STATUSES = frozenset({"draft","pending_approval","approved","scheduled","processing",
                      "published","partial","failed","needs_review","cancelled"})
# Conservative authoring limits; actual APIs, account permissions and policies
# must be checked before publishing. A limit is not an API capability guarantee.
TEXT_LIMITS = {"youtube": 5000, "facebook": 5000, "instagram": 2200, "threads": 500}
VIDEO_RECOMMENDATION = {"aspect_ratio":"9:16","width":1080,"height":1920,
                        "container":"mp4","video_codec":"h264","audio_codec":"aac"}
URL_PATTERN = re.compile(r"^https://",re.I)


def safe_url(value):
    if not value:
        return True
    try:
        parsed = urlparse(str(value))
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def parse_time(value, tz_name="Africa/Cairo"):
    if not value:
        raise ValueError("SCHEDULE_TIME_REQUIRED")
    try:
        zone = ZoneInfo(tz_name)
        dt = datetime.fromisoformat(str(value).strip().replace("Z","+00:00"))
        return (dt.replace(tzinfo=zone) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (ValueError, KeyError) as exc:
        raise ValueError("SCHEDULE_TIME_INVALID") from exc


def normalize(row):
    """Validate one dict from Content_Queue; never silently trim content."""
    item = {key:str(row.get(key,"") or "").strip() for key in COLUMNS}
    if not item["task_id"]:
        raise ValueError("TASK_ID_REQUIRED")
    kind = item["content_type"].lower()
    if kind not in TYPES:
        raise ValueError("CONTENT_TYPE_INVALID")
    targets = list(dict.fromkeys(s.strip().lower() for s in item["destinations"].split(",") if s.strip()))
    if not targets or any(s not in DESTINATIONS for s in targets):
        raise ValueError("DESTINATIONS_INVALID")
    if item["status"] and item["status"].lower() not in STATUSES:
        raise ValueError("STATUS_INVALID")
    if item["approval"].lower() not in {"yes","no","approved","pending","rejected"}:
        raise ValueError("APPROVAL_INVALID")
    if kind == "video" and not item["video_url"]:
        raise ValueError("VIDEO_URL_REQUIRED")
    if kind == "image" and not item["image_url"]:
        raise ValueError("IMAGE_URL_REQUIRED")
    if kind == "link" and not item["link_url"]:
        raise ValueError("LINK_URL_REQUIRED")
    if kind == "text" and not item["text"]:
        raise ValueError("TEXT_REQUIRED")
    for key in ("link_url","image_url","video_url"):
        if not safe_url(item[key]):
            raise ValueError(key.upper()+"_INVALID")
    rule = item["repeat_rule"].lower() or "none"
    if rule not in {"none","daily","weekly","monthly"}:
        raise ValueError("REPEAT_RULE_INVALID")
    if rule != "none" and not item["scheduled_at"]:
        raise ValueError("REPEAT_REQUIRES_SCHEDULE")
    if item["scheduled_at"]:
        parse_time(item["scheduled_at"],item["timezone"] or "Africa/Cairo")
    item["destinations"] = targets
    item["repeat_rule"] = rule
    item["content_type"] = kind
    return item


def plan(row, now=None):
    """Pure dry-run: no upload, no status mutation, no scheduling side effect."""
    item = normalize(row)
    if item["approval"].lower() not in {"yes","approved"}:
        return {"task_id":item["task_id"],"state":"awaiting_approval","targets":[]}
    if item["status"].lower() in TERMINAL:
        return {"task_id":item["task_id"],"state":"already_final","targets":[]}
    if item["status"].lower() in {"processing","partial","needs_review","failed"}:
        return {"task_id":item["task_id"],"state":"manual_review","targets":[]}
    if item["status"].lower() in {"draft","pending_approval"}:
        return {"task_id":item["task_id"],"state":"awaiting_approval","targets":[]}
    now = now or datetime.now(timezone.utc)
    if item["scheduled_at"] and parse_time(item["scheduled_at"],item["timezone"] or "Africa/Cairo") > now:
        return {"task_id":item["task_id"],"state":"scheduled","targets":[]}
    targets=[]
    for destination in item["destinations"]:
        text=item["text"]
        if len(text)>TEXT_LIMITS[destination]:
            targets.append({"platform":destination,"state":"needs_text_edit",
                            "max_chars":TEXT_LIMITS[destination]})
        elif destination=="youtube" and item["content_type"]!="video":
            targets.append({"platform":destination,"state":"unsupported_content_type"})
        elif destination=="instagram" and item["content_type"] in {"text","link"}:
            targets.append({"platform":destination,"state":"requires_media"})
        else:
            targets.append({"platform":destination,"state":"ready_for_adapter"})
    return {"task_id":item["task_id"],"state":"planned","targets":targets,
            "video_recommendation":VIDEO_RECOMMENDATION if item["content_type"]=="video" else None}


def next_occurrence(value, rule, tz_name="Africa/Cairo"):
    """Compute next local wall-clock occurrence, without auto-approving it."""
    current=parse_time(value,tz_name).astimezone(ZoneInfo(tz_name))
    if rule=="daily":
        future=current+timedelta(days=1)
    elif rule=="weekly":
        future=current+timedelta(days=7)
    elif rule=="monthly":
        month=current.month+1
        year=current.year+(month>12)
        month=1 if month>12 else month
        import calendar
        future=current.replace(year=year,month=month,day=min(current.day,calendar.monthrange(year,month)[1]))
    else:
        raise ValueError("REPEAT_RULE_INVALID")
    return future.isoformat()
