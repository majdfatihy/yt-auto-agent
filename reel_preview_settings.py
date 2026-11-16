"""Resolve exact owner-selected per-reel settings for Telegram previews.

Preview settings are provided by the Cloudflare Worker from its own KV defaults
plus per-reel overrides. Missing payload keeps legacy per-reel behavior.
"""
import json


def resolve_preview_settings(plan, clip_ids, raw):
    try:
        supplied = json.loads(raw) if raw else {}
    except (TypeError, ValueError) as exc:
        raise ValueError("PREVIEW_SETTINGS_INVALID_JSON") from exc
    if not isinstance(supplied, dict):
        raise ValueError("PREVIEW_SETTINGS_NOT_OBJECT")
    requested = {str(clip_id) for clip_id in clip_ids}
    if supplied and set(supplied) != requested:
        raise ValueError("PREVIEW_SETTINGS_IDS_MISMATCH")
    saved = plan.get("clip_settings") or {}
    if not isinstance(saved, dict):
        raise ValueError("PREVIEW_SETTINGS_SAVED_INVALID")
    result = {}
    for clip_id in clip_ids:
        key = str(clip_id)
        per_clip, incoming = saved.get(key, {}), supplied.get(key, {})
        if not isinstance(per_clip, dict) or not isinstance(incoming, dict):
            raise ValueError("PREVIEW_SETTINGS_CLIP_INVALID")
        if set(incoming) - {"captions", "blur", "privacy"}:
            raise ValueError("PREVIEW_SETTINGS_UNKNOWN_FIELD")
        merged = {"captions": True, "blur": "auto", "privacy": "public",
                  **per_clip, **incoming}
        if (type(merged["captions"]) is not bool
                or merged["blur"] not in ("auto", "on", "off")
                or merged["privacy"] not in ("public", "unlisted", "private")):
            raise ValueError("PREVIEW_SETTINGS_VALUES_INVALID")
        result[clip_id] = merged
    return result
