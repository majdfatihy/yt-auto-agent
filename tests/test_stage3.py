"""Stage 3 offline tests: clip-relative captions; no API or publication."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from captions_renderer import build_caption_events, write_ass, burn_captions
import transcription_pipeline as tp


class SubtitleEventTests(unittest.TestCase):
    def test_selected_clip_clock_resets_to_zero_and_boundary_clamps(self):
        events, approximate = build_caption_events([
            {'start': 28.0, 'end': 30.5, 'text': 'مقدمة المقطع'},
            {'start': 31.0, 'end': 34.0, 'text': 'شرح خطوة مهمة'},
            {'start': 35.0, 'end': 37.0, 'text': 'خارج الفترة'},
        ], 30.0, 35.0)
        self.assertEqual(events[0]['start'], 0)
        self.assertEqual(events[0]['end'], .5)
        self.assertEqual(events[1]['start'], 1)
        self.assertEqual(events[1]['end'], 4)
        self.assertEqual(len(events), 2)
        self.assertFalse(approximate)

    def test_word_cues_group_and_synchronize(self):
        words = [
            {'start': 60.00, 'end': 60.30, 'text': 'هنا'},
            {'start': 60.31, 'end': 60.58, 'text': 'هنشرح'},
            {'start': 60.60, 'end': 61.10, 'text': 'الطريقة'},
            {'start': 63.0, 'end': 64.0, 'text': 'النتيجة'},
        ]
        events, approximate = build_caption_events(words, 60.0, 65.0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['text'], 'هنا هنشرح الطريقة')
        self.assertEqual(events[0]['start'], 0)
        self.assertAlmostEqual(events[0]['end'], 1.1)
        self.assertEqual(events[1]['start'], 3)
        self.assertFalse(approximate)

    def test_long_segment_remains_inside_actual_segment(self):
        events, approximate = build_caption_events([
            {'start': 12, 'end': 19, 'text': 'دلوقتي هنعمل الشرح المفصل عن طريقة الاشتراك في الخدمة وازاي تحصل على الخصم خطوة بخطوة'}
        ], 10, 20)
        self.assertTrue(approximate)
        self.assertGreater(len(events), 1)
        self.assertAlmostEqual(events[0]['start'], 2)
        self.assertAlmostEqual(events[-1]['end'], 9)
        self.assertTrue(all(0 <= e['start'] < e['end'] <= 10 for e in events))

    def test_reject_untimed_or_silent_clip(self):
        with self.assertRaisesRegex(ValueError, 'CAPTION_NO_TIMED_SPEECH'):
            build_caption_events([], 1, 30)
        with self.assertRaises(ValueError):
            build_caption_events([{'start': 0, 'end': 5, 'text': 'كلام'}], 7, 30)

    def test_ass_injection_stripped(self):
        events, _ = build_caption_events([
            {'start': 0, 'end': 2, 'text': r'{\pos(0,0)}بداية \\ الكلام'}
        ], 0, 3)
        self.assertNotIn(r'\pos', events[0]['text'])
        with tempfile.TemporaryDirectory() as td:
            raw = write_ass(events, Path(td)/'clip.ass').read_text(encoding='utf-8-sig')
            self.assertIn('Mikhak', raw)
            self.assertIn('Dialogue:', raw)
            self.assertNotIn(r'\pos', raw)

    def test_groq_word_timestamps_keeps_default_segments(self):
        def response(payload):
            return Mock(json=lambda: payload, raise_for_status=lambda: None)
        with tempfile.TemporaryDirectory() as td, patch.dict('os.environ', {'GROQ_API_KEY': 'unit-test-not-real'}):
            p = Path(td)/'file.mp3'; p.write_bytes(b'12345')
            with patch.object(tp.requests, 'post', return_value=response({'words': [{'start': 1, 'end': 1.2, 'word': 'كلمة'}], 'segments': []})) as api:
                cues = tp.groq_transcribe(str(p), word_timestamps=True)
                self.assertEqual(cues[0]['text'], 'كلمة')
                self.assertEqual(api.call_args.kwargs['data']['timestamp_granularities[]'], 'word')
            with patch.object(tp.requests, 'post', return_value=response({'segments': [{'start': 1, 'end': 2, 'text': 'عبارة'}]})) as api:
                cues = tp.groq_transcribe(str(p))
                self.assertEqual(cues[0]['text'], 'عبارة')
                self.assertEqual(api.call_args.kwargs['data']['timestamp_granularities[]'], 'segment')

    def test_local_source_skips_youtube_caption_lookup(self):
        with patch.object(tp, 'youtube_captions', side_effect=AssertionError('youtube should not be called')), \
             patch.object(tp, 'audio_from_video', return_value='audio.mp3'), \
             patch.object(tp, 'groq_transcribe', return_value=[{'start':1,'end':2,'text':'كلام'}]) as groq, \
             patch.dict('os.environ', {'TRANSCRIPTION_PROVIDERS': 'groq'}):
            with tempfile.TemporaryDirectory() as td:
                source = Path(td)/'test.mp4'; source.write_bytes(b'fake')
                cues, provider = tp.obtain_timed_transcript('https://youtu.be/example', str(source),
                    prefer_local_audio=True, prefer_word_timestamps=True)
                self.assertEqual(provider, 'groq')
                self.assertEqual(len(cues), 1)
                groq.assert_called_once_with('audio.mp3', word_timestamps=True)

    def test_no_source_fails_closed(self):
        with self.assertRaises(RuntimeError):
            burn_captions('nonexisting.mp4', [{'start': 0, 'end': 2, 'text': 'اهلا'}], 0, 2)


if __name__ == '__main__':
    unittest.main()
