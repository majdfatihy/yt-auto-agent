import os
import unittest
from unittest.mock import patch

import text_ai
from reel_title_quality import valid_title


class TextProviderTests(unittest.TestCase):
    def test_gemini_fails_then_groq_succeeds_without_extra_keys(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-token"}, clear=False), \
             patch.object(text_ai, "_gemini_json", side_effect=ValueError("404")), \
             patch.object(text_ai, "_groq_json", return_value={"title": "طريقة شحن كارت تلدا بالخطوات"}) as groq:
            payload, provider = text_ai.generate_json(
                "JSON", "test-gemini", "CLIP_PLANNER_MODELS", lambda x:x)
        self.assertEqual(payload["title"], "طريقة شحن كارت تلدا بالخطوات")
        self.assertTrue(provider.startswith("groq:"))
        self.assertEqual(groq.call_args.args[0], "openai/gpt-oss-120b")

    def test_both_providers_fail_closed(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-token"}, clear=False), \
             patch.object(text_ai, "_gemini_json", side_effect=ValueError("404")), \
             patch.object(text_ai, "_groq_json", side_effect=ValueError("429")):
            with self.assertRaisesRegex(RuntimeError, "REEL_TEXT_AI_UNAVAILABLE"):
                text_ai.generate_json(
                    "JSON", "test-gemini", "CLIP_PLANNER_MODELS", lambda x:x)

    def test_candidate_validation_failure_tries_second_model(self):
        with patch.dict(os.environ, {"CLIP_PLANNER_MODELS": "gemini-3.8-flash,gemini-3.6-flash"}, clear=False), \
             patch.object(text_ai, "_gemini_json", side_effect=[
                 {"title": "مرحبا بكم يا شباب"},
                 {"title": "طريقة شحن كارت تلدا باستخدام إنستا باي"}]) as gemini:
            payload, provider = text_ai.generate_json(
                "JSON", "key", "CLIP_PLANNER_MODELS",
                lambda x: x if valid_title(x["title"]) else (_ for _ in ()).throw(ValueError("weak")))
        self.assertEqual(gemini.call_count, 2)
        self.assertEqual(provider, "gemini:gemini-3.6-flash")
        self.assertIn("تلدا", payload["title"])


class TitleQualityTests(unittest.TestCase):
    def test_block_bad_existing_bot_titles(self):
        old_titles = [
            "مقتطف: مرحبا بكم يا شباب وان شاء الله تكونوا بخير",
            "مقتطف: ومعتبرش نصيحة مالية او استثمارية",
            "مقتطف: المبلغ اللي انت عايز تطلبه وبعد كده",
            "مقتطف: اسمه طبعا ده بيكون ليه فروع",
            "مقتطف: هتكمل عملية التحويل هنا بيقول لك",
            "مرحبا بكم يا شباب وان شاء الله تكونوا بخير",
        ]
        for title in old_titles:
            with self.subTest(title=title):
                self.assertFalse(valid_title(title))

    def test_accept_coherent_title_when_grounded(self):
        title = "طريقة شحن كارت تلدا باستخدام إنستا باي"
        transcript = "شرحنا في الفيديو طريقة شحن كارت تلدا باستخدام إنستا باي"
        self.assertTrue(valid_title(title, transcript))
        self.assertFalse(valid_title(
            "كيفية تحديث نظام ويندوز في الكمبيوتر", transcript))


if __name__ == "__main__":
    unittest.main()
