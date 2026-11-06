"""Stage-2 offline unit tests: no credentials, network, video publication."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clip_planner import parse_clip_choices, plan_reference, record_preview_result


def sample_plan():
    return {
        'version': 1, 'status': 'awaiting_selection',
        'created_utc': '2026-11-04T07:13:07.382901+00:00',
        'video_url': 'https://youtu.be/wQUPA9K2MSY',
        'telegram_file_id': 'tg_sample', 'duration': 92.3,
        'clips': [
            {'id': 1, 'title': 'First', 'start_time': 0.0, 'end_time': 30.0},
            {'id': 2, 'title': 'Second', 'start_time': 30.0, 'end_time': 55.0},
            {'id': 3, 'title': 'Third', 'start_time': 55.0, 'end_time': 82.0},
        ]
    }


class ClipChoicesTests(unittest.TestCase):
    def test_single_and_multiple_and_arabic_digits(self):
        plan = sample_plan()
        self.assertEqual(parse_clip_choices('1', plan), [1])
        self.assertEqual(parse_clip_choices('1,3', plan), [1, 3])
        self.assertEqual(parse_clip_choices('١،٣', plan), [1, 3])
        self.assertEqual(parse_clip_choices('1.2.3', plan), [1, 2, 3])

    def test_unknown_duplicate_and_injection_rejected(self):
        for text in ('0', '4', '1,1', '1;3', '1 && publish', '', '-1'):
            with self.subTest(value=text), self.assertRaises(ValueError):
                parse_clip_choices(text, sample_plan())

    def test_reject_unexpected_plan_status(self):
        plan = sample_plan()
        plan['status'] = 'published'
        with self.assertRaises(ValueError):
            parse_clip_choices('1', plan)

    def test_reject_invalid_timestamp_and_title(self):
        plan = sample_plan()
        plan['clips'][0]['end_time'] = 400
        with self.assertRaises(ValueError):
            parse_clip_choices('1', plan)
        plan = sample_plan()
        plan['clips'][0]['title'] = ''
        with self.assertRaises(ValueError):
            parse_clip_choices('1', plan)

    def test_plan_reference_unchanged_after_preview_status(self):
        plan = sample_plan()
        reference = plan_reference(plan)
        plan['status'] = 'previewed_awaiting_approval'
        plan['previewed_ids'] = [1, 3]
        self.assertEqual(plan_reference(plan), reference)

    def test_record_stores_review_not_approval_or_publication(self):
        plan = sample_plan()
        with patch('clip_planner.load_clip_plan', return_value=plan), \
             patch('clip_planner.save_clip_plan') as saved:
            record_preview_result(plan_reference(plan), [1, 3], run_id='12345')
            result = saved.call_args.args[0]
            self.assertEqual(result['status'], 'previewed_awaiting_approval')
            self.assertEqual(result['previewed_ids'], [1, 3])
            self.assertNotIn('approved', result)


if __name__ == '__main__':
    unittest.main()
