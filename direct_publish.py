"""Direct and timed uploads of explicit owner-approved transcript-first reels.

Never require preview artifacts; never upload without a persisted approval
record. A reservation state is saved before touching the YouTube upload API.
Unknown outcomes remain locked for manual reconciliation, never retried.
"""
from datetime import datetime, timezone
import os
import re
from pathlib import Path

from clip_planner import load_clip_plan, save_clip_plan
from reel_history import load_history, has_overlap, record_published
from reel_metadata import parse_youtube_tags
from reel_title_quality import valid_title
from reel_transcript_store import load_clip_transcripts


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def publish_direct(expected_ref, selected, url, telegram_file_id,
                   render, caption_render, upload, notify,
                   fetch_telegram, fetch_drive):
    plan = load_clip_plan()
    if (not expected_ref or plan.get("created_utc") != expected_ref or
            plan.get("video_url") != url or
            plan.get("telegram_file_id", "") != telegram_file_id or
            plan.get("method") != "topic_transcript_ai"):
        raise RuntimeError("DIRECT_PUBLISH_PLAN_OR_SOURCE_MISMATCH")
    ids = [int(x) for x in selected.split(",") if x.isascii() and x.isdigit()]
    if not ids or len(ids) != len(set(ids)) or len(ids) > 40:
        raise RuntimeError("DIRECT_PUBLISH_IDS_INVALID")
    clips = {c["id"]:c for c in plan["clips"]}
    all_jobs = plan.get("clip_jobs") or {}
    history = (load_history(url) if os.getenv("GH_PAT") else [])
    for clip_id in ids:
        clip = clips.get(clip_id)
        job = all_jobs.get(str(clip_id))
        if not clip or not job or job.get("state") != "queued" or not job.get("approved_at"):
            raise RuntimeError(f"DIRECT_PUBLISH_NOT_APPROVED_OR_ALREADY_STARTED: {clip_id}")
        if job.get("privacy") not in ("public","private","unlisted"):
            raise RuntimeError("DIRECT_PUBLISH_PRIVACY_INVALID")
        if (has_overlap(history, clip["start_time"], clip["end_time"]) or
                any(x.get("clip_id") == clip_id for x in plan.get("published_results",[]))):
            raise RuntimeError("DIRECT_PUBLISH_ALREADY_UPLOADED")
        if (clip.get("reel_seo_version") != 3 or
                not valid_title(clip.get("reel_title")) or
                not clip["reel_title"].endswith(" " + str(clip["serial"])) or
                not isinstance(clip.get("reel_description"),str) or
                len(clip["reel_description"].splitlines()) != 4 or
                not clip.get("reel_language") == "ar"):
            raise RuntimeError(f"DIRECT_PUBLISH_SEO_NOT_READY: {clip_id}")
        parse_youtube_tags(clip.get("reel_tags_string"))
    # Timed speech was saved during transcription-first /prepare.
    # No second full-file ASR pass for each scheduled reel.
    full_text = load_clip_transcripts(plan)
    cues = full_text.get("cues", [])
    if not cues:
        raise RuntimeError("DIRECT_PUBLISH_TIMED_CUES_MISSING")
    # Only download video once within this runner, if multiple reels are selected.
    fetch_telegram()
    fetch_drive()
    if not os.getenv("SOURCE_VIDEO_FILE"):
        # YouTube sources are downloaded by the first render invocation.
        pass

    completed = []
    for clip_id in ids:
        latest = load_clip_plan()
        if latest.get("created_utc") != expected_ref:
            raise RuntimeError("DIRECT_PUBLISH_PLAN_CHANGED")
        current_job = (latest.get("clip_jobs") or {}).get(str(clip_id))
        if not current_job or current_job.get("state") != "queued":
            raise RuntimeError(f"DIRECT_PUBLISH_ALREADY_STARTED: {clip_id}")
        clip = next((x for x in latest["clips"] if x["id"] == clip_id), None)
        if not clip:
            raise RuntimeError("DIRECT_PUBLISH_CLIP_REMOVED")
        if has_overlap(load_history(url), clip["start_time"], clip["end_time"]):
            raise RuntimeError("DIRECT_PUBLISH_ALREADY_UPLOADED")
        current_job["state"] = "uploading"
        current_job["upload_started_at"] = utc_now()
        save_clip_plan(latest)
        # Do not move reservation backwards after a crash or unknown result.
        try:
            blur_mode = current_job.get("blur", "auto")
            render(url,clip["start_time"],clip["end_time"],blur_mode=blur_mode)
            video = "output_reel.mp4"
            if current_job.get("captions",True):
                video = caption_render(video,cues,clip["start_time"],clip["end_time"])
            if not Path(video).is_file() or Path(video).stat().st_size < 10000:
                raise RuntimeError("DIRECT_PUBLISH_MP4_NOT_RENDERED")
            # Same file is reused for the rest of the items this workflow run.
            cached = Path("input.mp4")
            if cached.is_file():
                os.environ["SOURCE_VIDEO_FILE"] = str(cached.resolve())
            result = upload(
                video, clip["reel_title"],clip["reel_description"],
                clip["reel_tags_string"],privacy=current_job["privacy"])
            video_id = re.search(r"https://youtu\.be/([A-Za-z0-9_-]{11})",result or "")
            if not str(result).startswith("✅") or not video_id:
                raise RuntimeError("DIRECT_PUBLISH_RESULT_UNCONFIRMED: " + str(result)[:300])
            # Saving source history BEFORE marking the item successful prevents
            # accidental repeat uploads even if later GitHub plan saves fail.
            record_published(url,clip["start_time"],clip["end_time"],
                             video_id.group(1),serial=clip["serial"],
                             publication_status=current_job["privacy"])
            current = load_clip_plan()
            job = current["clip_jobs"][str(clip_id)]
            if job.get("state") != "uploading":
                raise RuntimeError("DIRECT_PUBLISH_STATE_CHANGED_AFTER_UPLOAD")
            job["state"] = "published"
            job["published_at"] = utc_now()
            job["youtube_id"] = video_id.group(1)
            job["result"] = result
            current.setdefault("published_results",[]).append(
                {"clip_id":clip_id,"result":result})
            # Keep stable IDs/title sequence for source-wide navigation.
            current["last_published_utc"] = utc_now()
            save_clip_plan(current)
            completed.append(clip_id)
            notify(result)
        except Exception as exc:
            # An exception can happen after YouTube accepted bytes; NEVER
            # auto-retry the same item, even for network or timeout errors.
            latest = load_clip_plan()
            pending = (latest.get("clip_jobs") or {}).get(str(clip_id))
            if pending and pending.get("state") == "uploading":
                pending["state"] = "needs_review"
                pending["error"] = str(exc)[:240]
                save_clip_plan(latest)
            notify(f"⚠️ تعذر التأكد من نشر ريلز {clip_id}. تم إيقاف إعادة المحاولة حفاظًا على عدم التكرار. راجع /status.")
            raise
    notify("✅ انتهى رفع الريلز المعتمدة: " + ", ".join(map(str,completed)) +
           ". يمكن مراجعة باقي الريلز في /menu.")
    return completed
