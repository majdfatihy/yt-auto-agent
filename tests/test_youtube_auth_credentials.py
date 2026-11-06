"""Offline tests: cookie parsing, proxy redaction, bounded downloader fallback."""
import ast
import base64
import contextlib
import io
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import transcription_pipeline as tp

_AGENT = Path(__file__).with_name('agent.py').read_text(encoding='utf8')
_NODE = next(x for x in ast.parse(_AGENT).body if isinstance(x, ast.FunctionDef) and x.name == 'prepare_youtube_cookies')
module = ast.Module(body=[_NODE], type_ignores=[])
ns = {'Path': Path, 'base64': base64, 'time': time, 'binascii': __import__('binascii')}
exec(compile(module, '<agent-cookie-loader>', 'exec'), ns)
parse_cookie_secret = ns['prepare_youtube_cookies']
SAMPLE = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tdummy_secret\n'

class CookieTests(unittest.TestCase):
    def test_raw_netscape_preserves_contents(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/'cookies.txt'
            with contextlib.redirect_stdout(io.StringIO()) as cap:
                self.assertEqual(parse_cookie_secret(SAMPLE,str(target)),str(target))
            self.assertEqual(target.read_text(),SAMPLE)
            self.assertNotIn('dummy_secret',cap.getvalue())
            self.assertIn('LOGIN_MARKERS: YES',cap.getvalue())

    def test_base64_netscape_supported(self):
        data = base64.b64encode(SAMPLE.encode()).decode()
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/'cookies.txt'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(parse_cookie_secret(data,str(target)),str(target))
            self.assertEqual(target.read_text(),SAMPLE)

    def test_backslash_n_supported(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/'cookies.txt'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(parse_cookie_secret(SAMPLE.replace('\n','\\n'),str(target)),str(target))
            self.assertEqual(target.read_text(),SAMPLE)

    def test_invalid_secret_never_written(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/'cookies.txt'
            with contextlib.redirect_stdout(io.StringIO()) as cap:
                self.assertIsNone(parse_cookie_secret('not a cookies.txt',str(target)))
            self.assertFalse(target.exists())
            self.assertIn('INVALID_FORMAT',cap.getvalue())

    def test_non_youtube_cookies_rejected(self):
        text = '# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t9999999999\tSID\tsecret\n'
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/'cookies.txt'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(parse_cookie_secret(text,str(target)))
            self.assertFalse(target.exists())

class DownloadTests(unittest.TestCase):
    @patch.object(tp, 'audio_from_video', return_value='transcription_audio.mp3')
    @patch.object(tp.subprocess,'run')
    def test_login_check_one_public_fallback(self, run, extract):
        calls = []
        def simulate(cmd, **kw):
            calls.append(cmd)
            if len(calls)==1:
                return subprocess.CompletedProcess(cmd, 1,'','Sign in to confirm you’re not a bot')
            out = Path(cmd[cmd.index('-o')+1].replace('%(ext)s','m4a'))
            out.write_bytes(b'audio')
            return subprocess.CompletedProcess(cmd,0,'','')
        run.side_effect=simulate
        with patch.dict(os.environ, {'YT_DLP_PROXY':'http://alice:secret@proxy.example:8888'}):
            with contextlib.redirect_stdout(io.StringIO()) as cap:
                self.assertEqual(tp.download_youtube_audio('https://youtu.be/test','cookies.txt'),'transcription_audio.mp3')
        self.assertEqual(len(calls),2)
        self.assertIn('--cookies',calls[0])
        self.assertNotIn('--cookies',calls[1])
        self.assertIn('web_embedded', ' '.join(calls[1]))
        self.assertIn('--proxy', calls[0])
        self.assertNotIn('alice:secret',cap.getvalue())
        extract.assert_called_once()

    @patch.object(tp, 'audio_from_video')
    @patch.object(tp.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','ERROR: not a bot, foo SECRET=123'))
    def test_both_blocked_proper_failure(self,run,extract):
        with contextlib.redirect_stdout(io.StringIO()) as cap:
            self.assertIsNone(tp.download_youtube_audio('https://youtu.be/test','cookies.txt'))
        self.assertEqual(run.call_count,2)
        self.assertIn('ACCESS_DENIED_OR_UNAVAILABLE',cap.getvalue())
        self.assertNotIn('SECRET=123',cap.getvalue())
        extract.assert_not_called()

    @patch.object(tp.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','Private video'))
    def test_restricted_content_not_retried(self,run):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(tp.download_youtube_audio('https://youtu.be/test'))
        self.assertEqual(run.call_count,1)

if __name__=='__main__': unittest.main()
