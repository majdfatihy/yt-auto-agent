import unittest
from unittest.mock import Mock
from reel_history import (canonical_source, variable_name, published_windows, has_overlap,
                          load_history, record_published, CONFIRMED_LEGACY_UPLOADS)

DRIVE = "https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view?usp=drivesdk"

class Reply:
    def __init__(self, code=200, value=None):
        self.status_code = code
        self.value = value
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP")
    def json(self):
        return self.value or {}

class FakeHttp:
    def __init__(self):
        self.value = None
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        if self.value is None:
            return Reply(404)
        return Reply(200, {"value": self.value})
    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        self.value = kwargs["json"]["value"]
        return Reply(201)
    def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url))
        self.value = kwargs["json"]["value"]
        return Reply(204)

class HistoryTests(unittest.TestCase):
    def test_source_key_stable_across_query_strings(self):
        plain = DRIVE.split("?")[0]
        self.assertEqual(canonical_source(DRIVE), canonical_source(plain))
        self.assertEqual(variable_name(DRIVE), variable_name(plain))

    def test_legacy_clip_is_excluded_by_confirmed_original_window(self):
        legacy = CONFIRMED_LEGACY_UPLOADS[canonical_source(DRIVE)]
        self.assertTrue(has_overlap(legacy, 66.5, 106.4))
        self.assertTrue(has_overlap(legacy, 68, 90))
        self.assertFalse(has_overlap(legacy, 106.4, 125))
        self.assertFalse(has_overlap(legacy, 0, 65))
        self.assertEqual(legacy[0]["youtube_video_id"], "nY9hYCj2fJ4")

    def test_persistent_storage_survives_multiple_prepares(self):
        import os
        from unittest.mock import patch
        client=FakeHttp()
        with patch.dict(os.environ, {"GH_PAT":"mock", "GITHUB_REPOSITORY":"owner/repo"}):
            history = load_history(DRIVE, http=client)
            self.assertEqual(len(history), 1)
            result = record_published(DRIVE, 110, 145, "abcdefghijk", http=client)
            self.assertEqual(len(result), 2)
            loaded = load_history(DRIVE, http=client)
            self.assertEqual(len(loaded), 2)
            self.assertTrue(has_overlap(loaded, 110, 145))
            self.assertIn("POST", [a for a,b in client.calls])
            result2=record_published(DRIVE,110,145,"abcdefghijk",http=client)
            self.assertEqual(len(result2),2)
            self.assertEqual([a for a,b in client.calls].count("POST"),1)

if __name__ == "__main__":
    unittest.main()
