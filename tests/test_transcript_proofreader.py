"""Offline tests; no API credentials needed."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcript_proofreader as pr


class ProofreaderTests(unittest.TestCase):
    ORIGINAL = [
        {'start': 0.10, 'end': 0.30, 'text': 'اهلا'},
        {'start': 0.31, 'end': 0.70, 'text': 'بكم'},
        {'start': 0.71, 'end': 1.10, 'text': 'فيديك'},
        {'start': 1.11, 'end': 1.45, 'text': 'يا'},
        {'start': 1.46, 'end': 1.80, 'text': 'شباب'},
    ]

    def test_gemini_edits_preserve_source_time_and_dont_mutate(self):
        raw = json.dumps({'edits': [{'index': 2, 'from': 'فيديك', 'to': 'فيديو'}]}, ensure_ascii=False)
        out, count = pr._apply_gemini_edits(self.ORIGINAL, raw)
        self.assertEqual((out[2]['text'], count), ('فيديو', 1))
        self.assertEqual(self.ORIGINAL[2]['text'], 'فيديك')
        self.assertEqual([(x['start'], x['end']) for x in out],
                         [(x['start'], x['end']) for x in self.ORIGINAL])

    def test_reject_changed_original_or_multiple_word_correction(self):
        for bad in [{'edits': [{'index': 2, 'from': 'wrong', 'to': 'فيديو'}]},
                    {'edits': [{'index': 2, 'from': 'فيديك', 'to': 'ده فيديو'}]},
                    {'edits': [{'index': 2, 'from': 'فيديك', 'to': r'{\\pos(1,1)}'}]}]:
            with self.assertRaises(ValueError):
                pr._apply_gemini_edits(self.ORIGINAL, json.dumps(bad, ensure_ascii=False))

    def test_reject_invalid_json_or_nonword_subtitles(self):
        with self.assertRaises((ValueError, TypeError)):
            pr._apply_gemini_edits(self.ORIGINAL, 'not-json')
        self.assertFalse(pr._word_cues_valid([{'start': 0, 'end': 1, 'text': 'اهلا بكم'}]))

    def test_fallback_deepgram_if_gemini_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td)/'a.mp3'
            audio.write_bytes(b'mp3')
            alt = [dict(x) for x in self.ORIGINAL]
            alt[2]['text'] = 'فيديو'
            with patch.object(pr, '_gemini_review', return_value=None), \
                 patch.object(pr, 'deepgram_transcribe', return_value=alt), \
                 patch.object(pr, 'assemblyai_transcribe') as assembly:
                result = pr.proofread_timed_cues(self.ORIGINAL, audio)
                self.assertEqual(result[2]['text'], 'فيديو')
                assembly.assert_not_called()

    def test_fallback_assemblyai_after_deepgram_error(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td)/'a.mp3'
            audio.write_bytes(b'mp3')
            alt = [dict(x) for x in self.ORIGINAL]
            with patch.object(pr, '_gemini_review', side_effect=RuntimeError('429')), \
                 patch.object(pr, 'deepgram_transcribe', side_effect=RuntimeError('503')), \
                 patch.object(pr, 'assemblyai_transcribe', return_value=alt):
                self.assertEqual(pr.proofread_timed_cues(self.ORIGINAL, audio), alt)

    def test_all_fail_keep_original(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td)/'a.mp3'
            audio.write_bytes(b'mp3')
            with patch.object(pr, '_gemini_review', return_value=None), \
                 patch.object(pr, 'deepgram_transcribe', return_value=[]), \
                 patch.object(pr, 'assemblyai_transcribe', side_effect=RuntimeError('none')):
                self.assertEqual(pr.proofread_timed_cues(self.ORIGINAL, audio), self.ORIGINAL)

    def test_fallback_cannot_silently_switch_to_truncated_result(self):
        self.assertFalse(pr._fallback_cues_valid(self.ORIGINAL, self.ORIGINAL[:2]))


    def test_manual_edit_overrides_gemini_on_same_word(self):
        from caption_revisions import apply_approved_edits
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / 'corrections.json'
            config.write_text(json.dumps({
                'video_url': 'https://youtu.be/example',
                'edits': [{'start': 0.71, 'from': 'فيديك', 'to': 'vidIQ'}],
            }, ensure_ascii=False), encoding='utf-8')
            original = [dict(c) for c in self.ORIGINAL]
            approved = apply_approved_edits(original, 'https://youtu.be/example', config)
            gemini_response = json.dumps({'edits': [
                {'index': 2, 'from': 'فيديك', 'to': 'فيديو'},
                {'index': 4, 'from': 'شباب', 'to': 'شباب!'},
            ]}, ensure_ascii=False)
            reviewed, _ = pr._apply_gemini_edits(original, gemini_response)
            result = pr.preserve_approved_word_edits(original, reviewed, approved)
            self.assertEqual(result[2]['text'], 'vidIQ')
            self.assertEqual(result[4]['text'], 'شباب!')
            self.assertEqual(original[2]['text'], 'فيديك')
            self.assertEqual([(c['start'], c['end']) for c in result],
                             [(c['start'], c['end']) for c in original])

    def test_manual_edit_with_alternate_asr_timings_falls_back_safely(self):
        original = [dict(c) for c in self.ORIGINAL]
        approved = [dict(c) for c in original]
        approved[2]['text'] = 'vidIQ'
        alternate = [dict(c) for c in original]
        alternate[2]['start'] += 0.02
        alternate[2]['text'] = 'فيديو'
        result = pr.preserve_approved_word_edits(original, alternate, approved)
        self.assertEqual(result, approved)
        self.assertEqual(alternate[2]['text'], 'فيديو')

    def test_no_manual_edits_keeps_other_provider_timings(self):
        original = [dict(c) for c in self.ORIGINAL]
        alternate = [dict(c) for c in original]
        alternate[2]['start'] += 0.02
        self.assertIs(pr.preserve_approved_word_edits(original, alternate, original), alternate)

    def test_preview_orders_manual_validation_before_ai_merging(self):
        # Catch an accidental return to applying edits to Gemini-changed words.
        code = (Path(__file__).resolve().parents[1] / 'agent.py').read_text(encoding='utf-8')
        manual = 'approved_cues = apply_approved_edits(original_cues, VIDEO_URL)'
        proofread = 'reviewed_cues = proofread_timed_cues(original_cues)'
        merge = 'preview_cues = preserve_approved_word_edits(original_cues, reviewed_cues, approved_cues)'
        self.assertLess(code.index(manual), code.index(proofread))
        self.assertLess(code.index(proofread), code.index(merge))


if __name__ == '__main__':
    unittest.main()
