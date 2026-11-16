import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from direct_publish import publish_direct


def base_plan():
    return {
        "method":"topic_transcript_ai",
        "created_utc":"2026-11-16T00:00:00Z",
        "video_url":"https://youtu.be/abcdefghijk",
        "telegram_file_id":"",
        "status":"awaiting_selection",
        "clips":[{
            "id":1,"serial":1,"start_time":1.0,"end_time":41.0,
            "reel_seo_version":3,"reel_language":"ar",
            "reel_title":"طريقة استقبال الحوالات الدولية على فودافون كاش #فودافون_كاش 1",
            "reel_description":"شرح استلام الحوالة الدولية بخطوات واضحة.\n"
                               "شرح طريقة استخدام محفظة فودافون كاش.\n"
                               "شرح حدود استقبال الأموال بوضوح.\n#فودافون_كاش #تحويلات",
            "reel_tags_string":"فودافون كاش, حوالات دولية, تحويلات خارجية, شحن محفظة, استلام الأموال, تطبيق فودافون",
        }],
        "clip_jobs":{"1":{
            "state":"queued","approved_at":"2026-11-16T00:00:00Z",
            "privacy":"unlisted","blur":"off","captions":False}},
        "published_results":[]
    }


class DirectPublishTests(unittest.TestCase):
    def run_upload(self, upload_result, expected_status):
        state=base_plan()
        notifications=[]
        mock_history=[]
        calls=[]
        def load():return copy.deepcopy(state)
        def save(plan):state.clear();state.update(copy.deepcopy(plan))
        def render(url,a,b,blur_mode):
            calls.append(("render",blur_mode))
            Path("output_reel.mp4").write_bytes(b"mp4" * 6000)
        def upload(*args,**kwargs):
            calls.append(("upload",kwargs.get("privacy")))
            return upload_result
        with tempfile.TemporaryDirectory() as tmp:
            cwd=Path.cwd()
            try:
                os.chdir(tmp)
                with patch("direct_publish.load_clip_plan",side_effect=load), \
                     patch("direct_publish.save_clip_plan",side_effect=save), \
                     patch("direct_publish.load_clip_transcripts",
                           return_value={"cues":[{"start":1,"end":41,"text":"كلام حقيقي"}]}), \
                     patch("direct_publish.load_history",return_value=mock_history), \
                     patch("direct_publish.record_published") as record:
                    try:
                        publish_direct(state["created_utc"],"1",state["video_url"],"",
                            render,lambda v,*rest:v,upload,notifications.append,
                            lambda:None,lambda:None)
                    except RuntimeError:
                        if expected_status=="published":raise
                    self.assertEqual(state["clip_jobs"]["1"]["state"],expected_status)
                    self.assertIn(("render","off"),calls)
                    self.assertIn(("upload","unlisted"),calls)
                    if expected_status=="published":
                        record.assert_called_once()
                        self.assertEqual(state["clip_jobs"]["1"]["youtube_id"],"abcdefghijk")
                        with self.assertRaisesRegex(RuntimeError,"ALREADY_STARTED"):
                            publish_direct(state["created_utc"],"1",state["video_url"],"",
                                render,lambda v,*rest:v,upload,notifications.append,
                                lambda:None,lambda:None)
                    else:
                        record.assert_not_called()
                        self.assertTrue(any("عدم التكرار" in x for x in notifications))
            finally:
                os.chdir(cwd)

    def test_confirmed_unlisted_upload_records_once(self):
        self.run_upload(
            "✅ يوتيوب: تم رفع الفيديو بنجاح بوضع unlisted (https://youtu.be/abcdefghijk)",
            "published")

    def test_unknown_result_locks_for_manual_review(self):
        self.run_upload("❌ يوتيوب خطأ: timeout","needs_review")

    def test_published_captions_match_the_saved_reviewed_preview(self):
        state = base_plan()
        state["clip_jobs"]["1"]["captions"] = True
        state["caption_cues_chunks"] = 1
        reviewed = [{"start": 1.0, "end": 1.5, "text": "تصحيح"}]
        captured = []
        def load(): return copy.deepcopy(state)
        def save(value): state.clear(); state.update(copy.deepcopy(value))
        def render(*_, **kwargs):
            Path("output_reel.mp4").write_bytes(b"mp4" * 6000)
        def caption(video, cues, start, end):
            captured.extend(cues)
            return video
        with tempfile.TemporaryDirectory() as folder:
            previous = Path.cwd()
            try:
                os.chdir(folder)
                with patch("direct_publish.load_clip_plan", side_effect=load), \
                     patch("direct_publish.save_clip_plan", side_effect=save), \
                     patch("direct_publish.load_clip_transcripts", return_value={
                         "cues": [{"start":1,"end":41,"text":"قديم"}]}), \
                     patch("direct_publish.load_caption_cues", return_value=reviewed), \
                     patch("direct_publish.load_history", return_value=[]), \
                     patch("direct_publish.record_published"):
                    publish_direct(state["created_utc"], "1", state["video_url"], "",
                        render, caption, lambda *a, **k: (
                            "✅ يوتيوب: تم رفع الفيديو بنجاح بوضع unlisted "
                            "(https://youtu.be/abcdefghijk)"),
                        lambda _: None, lambda:None, lambda:None)
            finally:
                os.chdir(previous)
        self.assertEqual(captured, reviewed)
        self.assertEqual(state["clip_jobs"]["1"]["state"], "published")


if __name__=="__main__":
    unittest.main()
