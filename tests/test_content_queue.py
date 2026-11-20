import unittest
from datetime import datetime, timezone
from content_queue import normalize, plan, next_occurrence

BASE={"task_id":"T-1","content_type":"video","video_url":"https://example.com/a.mp4",
      "destinations":"youtube,facebook,instagram,threads","approval":"approved",
      "status":"approved","text":"Video test","repeat_rule":"none"}

class QueueTests(unittest.TestCase):
    def test_video_all_platforms(self):
        result=plan(BASE,datetime(2026,11,20,tzinfo=timezone.utc))
        self.assertEqual(len(result["targets"]),4)
        self.assertTrue(all(t["state"]=="ready_for_adapter" for t in result["targets"]))
    def test_approval_required(self):
        self.assertEqual(plan({**BASE,"approval":"pending"})["state"],"awaiting_approval")
    def test_published_not_repeated(self):
        self.assertEqual(plan({**BASE,"status":"published"})["state"],"already_final")
    def test_unknown_outcome_not_retried(self):
        self.assertEqual(plan({**BASE,"status":"needs_review"})["state"],"manual_review")
    def test_future_scheduled(self):
        self.assertEqual(plan({**BASE,"scheduled_at":"2030-01-01T12:00:00+02:00"})["state"],"scheduled")
    def test_invalid_link(self):
        with self.assertRaises(ValueError): normalize({**BASE,"video_url":"http://example.com"})
    def test_text_only_instagram_blocked(self):
        row={**BASE,"content_type":"text","text":"hello","video_url":""}
        self.assertEqual(next(t for t in plan(row)["targets"] if t["platform"]=="instagram")["state"],"requires_media")
    def test_youtube_image_blocked(self):
        row={**BASE,"content_type":"image","image_url":"https://example.com/a.png"}
        self.assertEqual(next(t for t in plan(row)["targets"] if t["platform"]=="youtube")["state"],"unsupported_content_type")
    def test_text_length_review(self):
        row={**BASE,"text":"a"*501}
        self.assertEqual(next(t for t in plan(row)["targets"] if t["platform"]=="threads")["state"],"needs_text_edit")
    def test_month_end(self):
        self.assertTrue(next_occurrence("2026-01-31T10:00:00+02:00","monthly").startswith("2026-02-28T10:00:00"))

if __name__=="__main__":
    unittest.main()
