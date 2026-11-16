"""Optional, explicit YouTube public scheduling in Egypt time.

No schedule is invented. Private is the default. The user supplies the first
local datetime and, for multiple reels, an explicit spacing in hours.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo("Africa/Cairo")


def schedule_utc(plan, index, total, now=None):
    details = plan.get("schedule")
    if not details:
        return None
    if not isinstance(details, dict):
        raise ValueError("REEL_SCHEDULE_INVALID")
    supplied = details.get("first_local")
    if not isinstance(supplied, str):
        raise ValueError("REEL_SCHEDULE_MISSING_DATE")
    try:
        first = datetime.strptime(supplied, "%Y-%m-%d %H:%M").replace(tzinfo=CAIRO)
    except ValueError as exc:
        raise ValueError("REEL_SCHEDULE_FORMAT: YYYY-MM-DD HH:MM Cairo") from exc
    interval = details.get("spacing_hours")
    if total > 1 and (type(interval) is not int or not 1 <= interval <= 168):
        raise ValueError("REEL_BATCH_SCHEDULE_NEEDS_SPACING_HOURS")
    if total == 1:
        interval = 0
    target = first + timedelta(hours=interval * index)
    now = now or datetime.now(timezone.utc)
    if target.astimezone(timezone.utc) <= now + timedelta(minutes=20):
        raise ValueError("REEL_SCHEDULE_PUBLISH_TIME_MUST_BE_FUTURE")
    return target.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_recording_date(value, now=None):
    """No synthetic midnight timestamp is assigned from a date-only claim."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("REEL_RECORDING_DATETIME_INVALID")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("REEL_RECORDING_DATETIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise ValueError("REEL_RECORDING_DATETIME_NEEDS_TIMEZONE")
    now = now or datetime.now(timezone.utc)
    if parsed.astimezone(timezone.utc) > now:
        raise ValueError("REEL_RECORDING_DATETIME_IN_FUTURE")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
