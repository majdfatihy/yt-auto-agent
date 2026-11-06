
"""Offline regression tests for /prepare and /clip YouTube download paths."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_source as ys
import transcription_pipeline as tp

URL = 'https://www.youtube.com/watch?v=BAk0x_TEST1'


class YouTubeDownloadTests(unittest.TestCase):
    def test_video_format_and_ejs_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(cmd, **kwargs):
                self.assertEqual(cmd[0], 'yt-dlp')
                self.assertIn('--no-playlist', cmd)
                self.assertIn('bv*[height<=1080]+ba/b[height<=1080]/b', cmd)
                (Path(directory)/'input.mp4').write_bytes(b'good video')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(ys.subprocess, 'run', side_effect=fake_run):
                result = ys.download_youtube_video(URL, directory=directory)
            self.assertEqual(result.name, 'input.mp4')

    def test_fallback_only_when_format_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if len(calls) == 1:
                    return subprocess.CompletedProcess(cmd, 1, '', 'Requested format is not available')
                (Path(directory)/'input.webm').write_bytes(b'valid')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(ys.subprocess, 'run', side_effect=fake_run):
                path = ys.download_youtube_video(URL, directory=directory)
            self.assertEqual(path.suffix, '.webm')
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][calls[1].index('--format') + 1], 'bv*+ba/b')

    def test_auth_denied_fails_fast_and_never_fakes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ys.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['yt-dlp'], 1, '', 'Sign in to confirm you are not a bot')) as run:
                with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_AUTH_REQUIRED'):
                    ys.download_youtube_video(URL, directory=directory)
            self.assertEqual(run.call_count, 1)

    def test_403_po_token_restriction_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ys.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, '', 'ERROR: HTTP Error 403: Forbidden')) as run:
                with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_ACCESS_RESTRICTED'):
                    ys.download_youtube_audio(URL, directory=directory)
            self.assertEqual(run.call_count, 1)

    def test_transient_error_retries_and_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = 0
            def fake_run(cmd, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return subprocess.CompletedProcess(cmd, 1, '', 'Connection reset by peer')
                (Path(directory)/'source.mp3').write_bytes(b'valid')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(ys.subprocess, 'run', side_effect=fake_run), patch.object(ys.time, 'sleep'):
                path = ys.download_youtube_audio(URL, directory=directory)
            self.assertEqual(path.name, 'source.mp3')
            self.assertEqual(calls, 2)

    def test_audio_download_uses_audio_only_with_no_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(cmd, **kwargs):
                self.assertIn('--extract-audio', cmd)
                self.assertIn('--no-playlist', cmd)
                self.assertEqual(cmd[cmd.index('--format') + 1], 'ba/b')
                (Path(directory)/'source.mp3').write_bytes(b'valid')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(ys.subprocess, 'run', side_effect=fake_run):
                ys.download_youtube_audio(URL, directory=directory)

    def test_download_does_not_overwrite_existing_video(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'input.mp4').write_bytes(b'existing')
            with patch.object(ys.subprocess, 'run') as run:
                with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_STALE_INPUT_FILE'):
                    ys.download_youtube_video(URL, directory=directory)
            run.assert_not_called()
            self.assertEqual((Path(directory)/'input.mp4').read_bytes(), b'existing')

    def test_zero_byte_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(cmd, **kwargs):
                (Path(directory)/'source.mp3').touch()
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(ys.subprocess, 'run', side_effect=fake_run):
                with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_AUDIO_MISSING'):
                    ys.download_youtube_audio(URL, directory=directory)

    def test_cookies_only_when_configured(self):
        self.assertNotIn('--cookies', ys._base_command())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cookies.txt'
            path.write_text('# Netscape HTTP Cookie File\n')
            cmd = ys._base_command(str(path))
            self.assertEqual(cmd[cmd.index('--cookies') + 1], str(path))
        with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_COOKIES_FILE_MISSING'):
            ys._base_command('absent-cookies.txt')

    def test_missing_ytdlp_reports_real_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ys.subprocess, 'run', side_effect=FileNotFoundError):
                with self.assertRaisesRegex(ys.YouTubeSourceError, 'YTDLP_NOT_INSTALLED'):
                    ys.download_youtube_audio(URL, directory=directory)

    def test_prepare_fallback_downloads_audio_and_transcribes(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory)/'source.mp3'
            audio.write_bytes(b'stub')
            with patch.object(tp, 'youtube_captions', return_value=[]), \
                 patch.object(tp, 'download_youtube_audio', return_value=audio) as download, \
                 patch.object(tp, 'groq_transcribe', return_value=[{'start': 0, 'end': 1, 'text': 'النص'}]), \
                 patch.object(tp, 'audio_from_video', side_effect=AssertionError('no re-encode')), \
                 patch.dict(os.environ, {'TRANSCRIPTION_PROVIDERS': 'groq'}):
                cues, source = tp.obtain_timed_transcript(URL)
            self.assertEqual(source, 'groq')
            self.assertEqual(cues[0]['text'], 'النص')
            download.assert_called_once_with(URL, None)

    def test_prepare_prefers_valid_subtitles_and_skips_download(self):
        cues = [{'start': 0, 'end': 1, 'text': 'السطر'}]
        with patch.object(tp, 'youtube_captions', return_value=cues), \
             patch.object(tp, 'download_youtube_audio', side_effect=AssertionError('audio should not download')):
            self.assertEqual(tp.obtain_timed_transcript(URL), (cues, 'youtube_captions'))

    def test_prepare_cannot_claim_success_when_download_auth_blocked(self):
        with patch.object(tp, 'youtube_captions', return_value=[]), \
             patch.object(tp, 'download_youtube_audio', side_effect=ys.YouTubeSourceError('YOUTUBE_AUTH_REQUIRED')):
            with self.assertRaisesRegex(ys.YouTubeSourceError, 'YOUTUBE_AUTH_REQUIRED'):
                tp.obtain_timed_transcript(URL)

    def test_local_video_preferred_audio_not_downloaded_from_youtube(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'input.mp4'
            file.write_bytes(b'stub')
            with patch.object(tp, 'youtube_captions', side_effect=AssertionError('no youtube')), \
                 patch.object(tp, 'download_youtube_audio', side_effect=AssertionError('no youtube')), \
                 patch.object(tp, 'audio_from_video', return_value='test.mp3'), \
                 patch.object(tp, 'groq_transcribe', return_value=[{'start':0,'end':1,'text':'صوت'}]), \
                 patch.dict(os.environ, {'TRANSCRIPTION_PROVIDERS': 'groq'}):
                cues, source = tp.obtain_timed_transcript(URL, video_path=str(file), prefer_local_audio=True)
            self.assertEqual(source, 'groq')

    def test_workflow_has_ejs_runtime_and_package_dependency(self):
        project = Path(__file__).resolve().parents[1]
        workflow = (project/'.github/workflows/main.yml').read_text()
        deps = (project/'requirements.txt').read_text()
        self.assertIn('denoland/setup-deno@v2', workflow)
        self.assertIn('yt-dlp[default]', deps)


if __name__ == '__main__':
    unittest.main()

class AgentPathTests(unittest.TestCase):
    def test_clip_invokes_real_youtube_downloader_and_renders_validated_source(self):
        """Exercise the actual agent function without importing external Google SDKs."""
        import ast
        import math
        import shutil
        agent_file = Path(__file__).resolve().parents[1] / 'agent.py'
        tree = ast.parse(agent_file.read_text(encoding='utf-8'))
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                    and x.name == 'download_and_convert_to_reel')
        namespace = {
            'Path': Path, 'os': os, 'math': math, 'shutil': shutil,
            'subprocess': subprocess, 'COOKIES_FILE': None,
            'print': lambda *a: None
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(agent_file), 'exec'), namespace)
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {'SOURCE_VIDEO_FILE': '', 'TELEGRAM_FILE_ID': ''}):
                    def download(url, cookies):
                        self.assertEqual(url, URL)
                        self.assertIsNone(cookies)
                        Path('input.mp4').write_bytes(b'downloaded mp4 stub')
                        return Path('input.mp4')
                    def ffmpeg_and_probe(cmd, **kwargs):
                        if cmd[0] == 'ffprobe':
                            return subprocess.CompletedProcess(cmd, 0, '120.0\n', '')
                        self.assertEqual(cmd[0], 'ffmpeg')
                        self.assertIn('input.mp4', cmd)
                        Path('output_reel.mp4').write_bytes(b'0' * 15000)
                        return subprocess.CompletedProcess(cmd, 0)
                    namespace['download_youtube_video'] = Mock(side_effect=download)
                    with patch.object(subprocess, 'run', side_effect=ffmpeg_and_probe):
                        namespace['download_and_convert_to_reel'](URL, 10, 35)
                    namespace['download_youtube_video'].assert_called_once_with(URL, None)
            finally:
                os.chdir(previous)
