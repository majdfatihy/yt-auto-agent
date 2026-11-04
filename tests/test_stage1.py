"""Offline stage-1 tests; no API calls, video edits, or publication."""
import os
import sys
import json
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clip_planner


class TestClipPlanner(unittest.TestCase):
    def setUp(self):
        self.cues = [
            {"start": i * 4.0, "end": i * 4.0 + 3.5,
             "text": f"شرح ميزة رقم {i} في تحسين قناة يوتيوب وخطوات التجربة"}
            for i in range(23)
        ]

    def test_fallback_with_groq_timing(self):
        with patch.object(clip_planner, '_gemini_candidates', return_value=[]):
            plan = clip_planner.build_clip_plan('https://youtu.be/abc', 'file_abc', self.cues, 92.3, '')
        self.assertEqual(plan['status'], 'awaiting_selection')
        self.assertEqual(len(plan['clips']), 3)
        self.assertEqual(plan['method'], 'timed_transcript_fallback')
        self.assertEqual([c['id'] for c in plan['clips']], [1, 2, 3])
        for c in plan['clips']:
            self.assertTrue(15 <= c['end_time'] - c['start_time'] <= 59)
            self.assertLessEqual(c['end_time'], 92.3)
            self.assertIn('مقتطف:', c['title'])
        self.assertIn('لم يتم قص أي فيديو', clip_planner.format_clip_plan(plan))

    def test_grounded_suggestions_preferred(self):
        candidates = [
            {"title": f"شرح الميزة {i}", "start_time": i * 25, "end_time": i * 25 + 30}
            for i in range(3)
        ]
        with patch.object(clip_planner, '_gemini_candidates', return_value=candidates):
            plan = clip_planner.build_clip_plan('https://youtu.be/abc', '', self.cues, 92.3, 'test')
        self.assertEqual(plan['method'], 'gemini')
        self.assertEqual([c['start_time'] for c in plan['clips']], [0.0, 25.0, 50.0])

    def test_reject_hallucinated_timestamps(self):
        self.assertIsNone(clip_planner._validate({"title": "موضوع", "start_time": 1000, "end_time": 1045},
                                                 self.cues, 92.3))
        self.assertIsNone(clip_planner._validate({"title": "موضوع", "start_time": -1, "end_time": 50},
                                                 self.cues, 92.3))
        self.assertIsNone(clip_planner._validate({"title": "موضوع", "start_time": 1, "end_time": 8},
                                                 self.cues, 92.3))

    def test_missing_timing_refuses(self):
        with self.assertRaises(ValueError):
            clip_planner.build_clip_plan('https://youtu.be/abc', '', [], 92.3, '')

    def test_store_creates_variable(self):
        plan = {"clips": [{"id": 1, "title": "x", "start_time": 1, "end_time": 20}]}
        with patch.dict(os.environ, {"GH_PAT": "unit-test-only", "GITHUB_REPOSITORY": "example/repo"}):
            with patch.object(clip_planner.requests, 'get', return_value=Mock(status_code=404)):
                with patch.object(clip_planner.requests, 'post', return_value=Mock(status_code=201)) as sender:
                    clip_planner.save_clip_plan(plan)
                    self.assertEqual(sender.call_args.kwargs['json']['name'], 'TELEGRAM_CLIP_PROPOSALS')
                    self.assertEqual(json.loads(sender.call_args.kwargs['json']['value']), plan)

    def test_store_updates_variable(self):
        plan = {"clips": []}
        with patch.dict(os.environ, {"GH_PAT": "unit-test-only", "GITHUB_REPOSITORY": "example/repo"}):
            with patch.object(clip_planner.requests, 'get', return_value=Mock(status_code=200)):
                with patch.object(clip_planner.requests, 'patch', return_value=Mock(status_code=204)) as updater:
                    clip_planner.save_clip_plan(plan)
                    self.assertIn('TELEGRAM_CLIP_PROPOSALS', updater.call_args.args[0])

    def test_storage_requires_pat(self):
        with patch.dict(os.environ, {"GH_PAT": "", "GITHUB_REPOSITORY": "example/repo"}):
            with self.assertRaises(RuntimeError):
                clip_planner.save_clip_plan({"clips": []})


if __name__ == '__main__':
    unittest.main()
