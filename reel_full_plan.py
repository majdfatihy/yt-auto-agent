"""Partition the whole source into distinct, non-overlapping short reels.

Previously uploaded source-time windows are removed BEFORE segmentation. The
same source interval cannot appear in two proposed clips. Titles here are
temporary navigation labels: publication SEO comes from the whole transcript
and each clip's speech in reel_metadata.py.
"""
import math
from datetime import datetime, timezone

from clip_planner import (_valid_cues, _speech_overlap, MIN_CLIP_SECONDS,
                          MAX_CLIP_SECONDS)
from reel_history import published_windows, overlaps

MAX_SOURCE_REELS = 40
TARGET_SECONDS = 43


def remaining_intervals(duration, published):
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("REEL_INVALID_SOURCE_DURATION")
    excluded = []
    for left, right in published:
        left, right = max(0.0, float(left)), min(float(duration), float(right))
        if left < right:
            excluded.append((left, right))
    excluded.sort()
    merged = []
    for a, b in excluded:
        if merged and a <= merged[-1][1] + 0.25:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    remaining = []
    cursor = 0.0
    for a, b in merged:
        if a - cursor >= MIN_CLIP_SECONDS:
            remaining.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor >= MIN_CLIP_SECONDS:
        remaining.append((cursor, duration))
    return remaining


def divide_gap(start, end, cues):
    length = end - start
    if length < MIN_CLIP_SECONDS:
        return []
    count = max(1, math.ceil(length / TARGET_SECONDS))
    while length / count > MAX_CLIP_SECONDS:
        count += 1
    if length / count < MIN_CLIP_SECONDS:
        count = max(1, int(length // MIN_CLIP_SECONDS))
    boundaries = [start]
    speech_ends = [c["end"] for c in cues if start < c["end"] < end]
    for i in range(1, count):
        expected = start + i * length / count
        prev = boundaries[-1]
        low = prev + MIN_CLIP_SECONDS
        high = end - (count - i) * MIN_CLIP_SECONDS
        near = [x for x in speech_ends
                if low <= x <= high and abs(x - expected) <= 4.5]
        chosen = min(near, key=lambda x: abs(x - expected)) if near else expected
        chosen = max(low, min(high, chosen))
        boundaries.append(chosen)
    boundaries.append(end)
    return [(round(a, 2), round(b, 2)) for a, b in zip(boundaries, boundaries[1:])
            if MIN_CLIP_SECONDS - 0.05 <= b - a <= MAX_CLIP_SECONDS + 0.05]


def build_remaining_plan(video_url, telegram_file_id, timed_cues, duration, history):
    cues = _valid_cues(timed_cues)
    if not cues:
        raise ValueError("لا يمكن تقسيم الفيديو دون تفريغ زمني موثوق.")
    published = published_windows(history)
    # Preserve unique sequence numbers across repeated /prepare requests.
    # Previously uploaded reels can be sparse if only a custom subset was approved.
    existing_serials = [x.get("serial") for x in history if isinstance(x, dict)
                        and type(x.get("serial")) is int and x["serial"] >= 1]
    serial_floor = max([len(published), *existing_serials])
    remaining = remaining_intervals(duration, published)
    clips = []
    excluded_silence = []
    truncated = []
    for start, end in remaining:
        for a, b in divide_gap(start, end, cues):
            if any(overlaps(a, b, p, q) for p, q in published):
                raise RuntimeError("REEL_SEGMENT_OVERLAPS_PUBLISHED: refuse duplication")
            if _speech_overlap(cues, a, b) < min(6, (b - a) * 0.16):
                excluded_silence.append([a, b])
                continue
            if len(clips) >= MAX_SOURCE_REELS:
                truncated.append([a, b])
                continue
            idx = len(clips) + 1
            serial = serial_floor + idx
            clips.append({
                "id": idx, "serial": serial,
                "title": f"الريلز {serial}: {int(a)}–{int(b)} ثانية من الفيديو",
                "start_time": a, "end_time": b,
                "source_window_key": f"{a:.2f}:{b:.2f}",
            })
    if not clips:
        return {
            "version": 2, "status": "all_published_or_no_speech",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "video_url": video_url, "telegram_file_id": telegram_file_id,
            "duration": round(duration, 2), "clips": [],
            "previously_uploaded": len(published),
            "unprocessed": truncated, "skipped_silence": excluded_silence,
        }
    # No assumptions about SEO scores. Choose first usable, non-introduction
    # speech-dense segment for the initial automatic preview.
    options = [clip for clip in clips if clip["start_time"] >= 10.0] or clips
    ranked = sorted(options, key=lambda c: (
        -_speech_overlap(cues, c["start_time"], c["end_time"])
        / (c["end_time"] - c["start_time"]), c["start_time"]))
    return {
        "version": 2, "status": "awaiting_selection",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "video_url": video_url, "telegram_file_id": telegram_file_id,
        "duration": round(duration, 2), "method": "full_video_nonoverlap",
        "recommended_clip_id": ranked[0]["id"], "clips": clips,
        "previously_uploaded": len(published), "unprocessed": truncated,
        "skipped_silence": excluded_silence,
    }
