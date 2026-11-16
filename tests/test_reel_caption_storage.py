"""Offline tests: reviewed caption cues survive preview and are hash-checked."""
import copy
import os
import unittest
from unittest.mock import patch

from reel_transcript_store import store_caption_cues, load_caption_cues


class Response:
    def __init__(self, status, value=None):
        self.status_code = status
        self.value = value

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP " + str(self.status_code))

    def json(self):
        return {"value": self.value}


class MemoryGitHub:
    def __init__(self):
        self.values = {}

    def get(self, url, **_):
        name = url.split("/")[-1]
        return Response(200, self.values[name]) if name in self.values else Response(404)

    def post(self, url, json, **_):
        self.values[json["name"]] = json["value"]
        return Response(201)

    def patch(self, url, json, **_):
        self.values[url.split("/")[-1]] = json["value"]
        return Response(204)


class CaptionStorageTests(unittest.TestCase):
    CUES = [{"start": 0.1, "end": 0.3, "text": "تصحيح"},
            {"start": 0.31, "end": 0.6, "text": "واضح"}]

    def setUp(self):
        self.server = MemoryGitHub()
        self.plan = {"created_utc": "2026-11-16T00:00:00Z"}
        self.tokens = patch.dict(os.environ,
            {"GH_PAT": "test", "GITHUB_REPOSITORY": "owner/repo"})
        self.tokens.start()

    def tearDown(self):
        self.tokens.stop()

    def test_roundtrip_and_replace_reviewed_cues(self):
        self.assertIsNone(load_caption_cues(self.plan, http=self.server))
        store_caption_cues(self.plan, self.CUES, http=self.server)
        self.assertEqual(load_caption_cues(self.plan, http=self.server), self.CUES)
        corrected = copy.deepcopy(self.CUES)
        corrected[0]["text"] = "تم"
        store_caption_cues(self.plan, corrected, http=self.server)
        self.assertEqual(load_caption_cues(self.plan, http=self.server), corrected)

    def test_corrupted_cue_blob_is_rejected_without_raw_fallback(self):
        store_caption_cues(self.plan, self.CUES, http=self.server)
        self.server.values["TELEGRAM_REEL_CAPTION_0"] = "not-base64"
        with self.assertRaisesRegex(RuntimeError, "CORRUPTED"):
            load_caption_cues(self.plan, http=self.server)

    def test_ref_mismatch_cannot_reuse_cues_for_different_source(self):
        store_caption_cues(self.plan, self.CUES, http=self.server)
        wrong = dict(self.plan, created_utc="2026-11-17T00:00:00Z")
        with self.assertRaisesRegex(RuntimeError, "PLAN_MISMATCH"):
            load_caption_cues(wrong, http=self.server)

    def test_empty_cues_not_saved(self):
        with self.assertRaisesRegex(RuntimeError, "EMPTY"):
            store_caption_cues(self.plan, [], http=self.server)


if __name__ == "__main__":
    unittest.main()
