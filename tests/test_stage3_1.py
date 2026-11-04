import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from captions_renderer import build_caption_events, write_ass, burn_captions, _dialogue_spans, _verify_font
from caption_revisions import apply_approved_edits

class Stage31CaptionTests(unittest.TestCase):
    WORDS = [
        {'start':10.0,'end':10.3,'text':'أهلاً'},
        {'start':10.31,'end':10.60,'text':'بكم'},
        {'start':10.61,'end':10.95,'text':'يا'},
        {'start':10.96,'end':11.4,'text':'شباب'},
    ]

    def test_clip_clock_and_word_timing_preserved(self):
        cards, approx = build_caption_events(self.WORDS, 10, 12)
        self.assertFalse(approx)
        self.assertEqual(len(cards), 1)
        self.assertEqual([round(w['start'], 2) for w in cards[0]['words']], [0, .31, .61, .96])
        spans = list(_dialogue_spans(cards[0]))
        self.assertTrue(any('\\1c&H0000FFFF&' in row[2] for row in spans))
        self.assertTrue(any('شباب' in row[2] and 'أهلاً' in row[2] for row in spans))
        self.assertAlmostEqual(spans[0][0], 0)
        self.assertAlmostEqual(spans[-1][1], 1.4)

    def test_ass_uses_mikhak_black_and_yellow_per_spoken_word(self):
        with tempfile.TemporaryDirectory() as td:
            cards, _ = build_caption_events(self.WORDS, 10, 12)
            ass = write_ass(cards, Path(td)/'preview.ass').read_text(encoding='utf-8-sig')
            self.assertIn('Style: Arabic,Mikhak,86,', ass)
            lines = [ln for ln in ass.splitlines() if ln.startswith('Dialogue:')]
            self.assertGreaterEqual(len(lines), 4)
            self.assertIn(r'{\1c&H0000FFFF&}أهلاً', lines[0])
            self.assertIn(r'{\1c&H0000FFFF&}بكم', ''.join(lines))

    def test_ass_injection_and_bidi_control_are_stripped(self):
        events, _ = build_caption_events([{'start':0,'end':1,'text':r'{\pos(1,1)}أهلا'}],0,1)
        with tempfile.TemporaryDirectory() as td:
            ass = write_ass(events, Path(td)/'sample.ass').read_text('utf-8-sig')
        self.assertNotIn(r'\pos', ass)

    def test_no_font_does_not_substitute(self):
        with self.assertRaisesRegex(RuntimeError, 'MIKHAK_BLACK_MISSING'):
            _verify_font('/tmp/definitely-not-mikhak-black.ttf', [{'text':'مرحبا'}])

    def test_reject_wrong_font_identity_instead_of_using_other_font(self):
        from importlib.util import spec_from_file_location, module_from_spec
        installer_path=Path(__file__).resolve().parents[1]/'scripts/install_mikhak.py'
        spec=spec_from_file_location('mikhak_installer',installer_path)
        module=module_from_spec(spec)
        spec.loader.exec_module(module)
        available=Path('/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf')
        if available.exists():
            with self.assertRaisesRegex(RuntimeError,'WRONG_FONT'):
                module.validate_font(available)

    def test_reject_segment_only_without_word_timings(self):
        with tempfile.TemporaryDirectory() as td:
            mp4=Path(td)/'input.mp4';mp4.write_bytes(b'1'*1100)
            with self.assertRaisesRegex(RuntimeError,'CAPTION_WORD_TIMESTAMPS_REQUIRED'):
                burn_captions(mp4, [{'start':0,'end':1,'text':'بداية المقاطع'}], 0,1,
                              Path(td)/'out.mp4',Path(td)/'out.ass')

    def test_no_auto_rewrites(self):
        cues=[{'start':.3,'end':.5,'text':'فيديك'}]
        with tempfile.TemporaryDirectory() as td:
            out=apply_approved_edits(cues, 'https://example.com', Path(td)/'not-present.json')
            self.assertEqual(out,cues)

    def test_explicit_approved_edit_only(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'edits.json'
            p.write_text(json.dumps({'video_url':'https://youtu.be/x',
                    'edits':[{'start':1.23,'from':'فيديك','to':'vidIQ'}]}),encoding='utf-8')
            cues=[{'start':1.23,'end':1.66,'text':'فيديك'},{'start':3,'end':3.5,'text':'فيفيد'}]
            out=apply_approved_edits(cues,'https://youtu.be/x',p)
            self.assertEqual(out[0]['text'],'vidIQ')
            self.assertEqual(out[1]['text'],'فيفيد')
            self.assertEqual(cues[0]['text'],'فيديك')
            with self.assertRaisesRegex(ValueError,'SOURCE_MISMATCH'):
                apply_approved_edits(cues,'https://youtu.be/another',p)

if __name__ == '__main__':
    unittest.main()
