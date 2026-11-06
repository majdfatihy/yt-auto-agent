import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import transcript_proofreader as pr

class InvalidTimingsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / 'audio.mp3'
        self.audio.write_bytes(b'dummy')
        self.invalid = [{'start': 0.1, 'end': 1.0, 'text': 'اهلا بكم'},
                        {'start': 1.0, 'end': 2.0, 'text': 'يا شباب'}]
        self.valid = [{'start': .1, 'end': .4, 'text': 'اهلا'},
                      {'start': .4, 'end': 1., 'text': 'بكم'},
                      {'start': 1., 'end': 1.5, 'text': 'يا'},
                      {'start': 1.5, 'end': 2., 'text': 'شباب'}]

    def test_invalid_source_recovers_word_cues_then_gemini(self):
        corrected = [dict(c) for c in self.valid]
        corrected[0]['text'] = 'أهلا'
        with patch.object(pr, 'deepgram_transcribe', return_value=self.valid), \
             patch.object(pr, '_gemini_review', return_value=corrected) as gemini:
            result = pr.proofread_timed_cues(self.invalid, self.audio)
            self.assertEqual(result, corrected)
            gemini.assert_called_once_with(self.valid, self.audio)

    def test_invalid_source_no_alternative_blocks_preview(self):
        with patch.object(pr, 'deepgram_transcribe', return_value=[]), \
             patch.object(pr, 'assemblyai_transcribe', return_value=[]), \
             patch.object(pr, '_gemini_review') as gemini:
            with self.assertRaisesRegex(RuntimeError, 'CAPTION_WORD_TIMINGS_UNRECOVERABLE'):
                pr.proofread_timed_cues(self.invalid, self.audio)
            gemini.assert_not_called()

    def test_invalid_manual_edits_not_silently_discarded(self):
        approved = [dict(c) for c in self.invalid]
        approved[0]['text'] = 'أهلا بكم'
        with self.assertRaisesRegex(RuntimeError, 'CAPTION_MANUAL_EDIT_REALIGNMENT_REQUIRED'):
            pr.preserve_approved_word_edits(self.invalid, self.valid, approved)

if __name__ == '__main__':
    unittest.main()
