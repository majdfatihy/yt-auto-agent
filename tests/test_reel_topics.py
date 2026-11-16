import unittest
from unittest.mock import patch
from reel_topics import build_topic_plan


class TopicPlanTests(unittest.TestCase):
    def setUp(self):
        self.cues=[{"start":i*4.0,"end":i*4.0+4.0,
                    "text":"طريقة استقبال حوالة دولية على فودافون كاش من التطبيق البنكي"}
                   for i in range(35)]

    @patch("reel_topics._topic_candidates")
    def test_grounded_titles_and_separate_sequence(self,mock):
        mock.side_effect=lambda cues,a,b,limit,key:[
            {"start_time":a,"end_time":min(b,a+40),
             "title":"طريقة استقبال الحوالة الدولية على فودافون كاش",
             "hashtag":"#فودافون_كاش"}]
        plan,texts=build_topic_plan("https://youtu.be/abcdefghijk","",
                                  self.cues,140,[],"fake",59)
        self.assertEqual(plan["method"],"topic_transcript_ai")
        self.assertEqual(plan["status"],"awaiting_selection")
        self.assertEqual(plan["clips"][0]["id"],1)
        self.assertEqual(plan["clips"][0]["serial"],1)
        self.assertIn("#فودافون_كاش 1",plan["clips"][0]["title"])
        self.assertGreater(len(texts["1"]),50)
        self.assertFalse(plan["clips"][0]["seo_ready"] if "seo_ready" in plan["clips"][0] else False)

    @patch("reel_topics._topic_candidates",return_value=[])
    def test_no_random_fixed_duration_fallback(self,mock):
        with self.assertRaisesRegex(RuntimeError,"NO_GROUNDED_TOPICS"):
            build_topic_plan("https://youtu.be/abcdefghijk","",self.cues,140,[],"fake",59)

    @patch("reel_topics._topic_candidates")
    def test_prior_publication_excluded(self,mock):
        mock.side_effect=lambda cues,a,b,limit,key:[
            {"start_time":a,"end_time":min(b,a+40),
             "title":"طريقة استقبال الحوالة الدولية على فودافون كاش",
             "hashtag":"#فودافون_كاش"}]
        history=[{"start_time":0,"end_time":40,"serial":2,
                  "youtube_id":"abcdefghijk"}]
        plan,_=build_topic_plan("https://youtu.be/abcdefghijk","",
                               self.cues,140,history,"fake",59)
        self.assertEqual(plan["clips"][0]["serial"],3)
        self.assertGreaterEqual(plan["clips"][0]["start_time"],40)


if __name__=="__main__":
    unittest.main()
