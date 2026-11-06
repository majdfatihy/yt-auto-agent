"""Offline regression tests for the targeted /prepare YouTube-audio fallback."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name('transcription_pipeline.py')
spec = importlib.util.spec_from_file_location('transcription_pipeline', MODULE_PATH)
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)

SAMPLE = [{'start': 1.0, 'end': 2.0, 'text': 'مثال'}]
URL = 'https://youtu.be/nPESFeivFF8'


class YoutubePrepareFallbackTests(unittest.TestCase):
    @patch.object(tp, 'youtube_captions', return_value=SAMPLE)
    @patch.object(tp, 'download_youtube_audio')
    def test_captions_win_without_download(self, download, captions):
        self.assertEqual(tp.obtain_timed_transcript(URL), (SAMPLE, 'youtube_captions'))
        download.assert_not_called()

    @patch.dict(os.environ, {'TRANSCRIPTION_PROVIDERS': 'groq'}, clear=False)
    @patch.object(tp, 'youtube_captions', return_value=[])
    @patch.object(tp, 'download_youtube_audio', return_value='transcription_audio.mp3')
    @patch.object(tp, 'groq_transcribe', return_value=SAMPLE)
    def test_url_only_uses_audio_then_groq(self, groq, download, captions):
        self.assertEqual(tp.obtain_timed_transcript(URL, cookies_file='cookies.txt'), (SAMPLE, 'groq'))
        download.assert_called_once_with(URL, 'cookies.txt')
        groq.assert_called_once_with('transcription_audio.mp3', word_timestamps=False)

    @patch.dict(os.environ, {'TRANSCRIPTION_PROVIDERS': 'groq,deepgram,assemblyai,gemini'}, clear=False)
    @patch.object(tp, 'youtube_captions', return_value=[])
    @patch.object(tp, 'download_youtube_audio', return_value='transcription_audio.mp3')
    @patch.object(tp, 'groq_transcribe', side_effect=RuntimeError('mock provider failure'))
    @patch.object(tp, 'deepgram_transcribe', return_value=SAMPLE)
    @patch.object(tp, 'assemblyai_transcribe')
    def test_groq_failure_tries_deepgram(self, assemblyai, deepgram, groq, download, captions):
        self.assertEqual(tp.obtain_timed_transcript(URL), (SAMPLE, 'deepgram'))
        deepgram.assert_called_once_with('transcription_audio.mp3')
        assemblyai.assert_not_called()

    @patch.object(tp, 'youtube_captions', return_value=[])
    @patch.object(tp, 'download_youtube_audio', return_value=None)
    @patch.object(tp, 'groq_transcribe')
    def test_download_failure_does_not_fabricate_timing(self, groq, download, captions):
        self.assertEqual(tp.obtain_timed_transcript(URL), ([], 'none'))
        groq.assert_not_called()

    @patch.dict(os.environ, {'TRANSCRIPTION_PROVIDERS': 'groq'}, clear=False)
    @patch.object(tp, 'download_youtube_audio')
    @patch.object(tp, 'youtube_captions')
    @patch.object(tp, 'audio_from_video', return_value='transcription_audio.mp3')
    @patch.object(tp, 'groq_transcribe', return_value=SAMPLE)
    def test_local_audio_preferred_for_preview(self, groq, extract, captions, download):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / 'my-video.mp4'
            video.write_bytes(b'video-placeholder')
            self.assertEqual(tp.obtain_timed_transcript(URL, video_path=str(video),
                prefer_local_audio=True, prefer_word_timestamps=True), (SAMPLE, 'groq'))
            extract.assert_called_once_with(str(video))
            groq.assert_called_once_with('transcription_audio.mp3', word_timestamps=True)
        captions.assert_not_called()
        download.assert_not_called()

    @patch.object(tp, 'download_youtube_audio')
    @patch.object(tp, 'youtube_captions')
    def test_preview_without_local_audio_does_not_download(self, captions, download):
        self.assertEqual(tp.obtain_timed_transcript(URL, prefer_local_audio=True), ([], 'none'))
        captions.assert_not_called()
        download.assert_not_called()

    @patch.object(tp, 'audio_from_video', return_value='transcription_audio.mp3')
    @patch.object(tp.subprocess, 'run')
    def test_downloader_passes_cookies_and_transcodes(self, run, extract):
        def fake_run(cmd, **kwargs):
            target = cmd[cmd.index('-o') + 1].replace('%(ext)s', 'm4a')
            Path(target).write_bytes(b'not-real-audio')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        run.side_effect = fake_run
        self.assertEqual(tp.download_youtube_audio(URL, 'cookies.txt'), 'transcription_audio.mp3')
        self.assertIn('--cookies', run.call_args.args[0])
        self.assertIn('bestaudio/best', run.call_args.args[0])
        extract.assert_called_once()

    @patch.object(tp, 'audio_from_video')
    @patch.object(tp.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'ERROR: 403 Forbidden'))
    def test_download_403_is_reported_without_calling_asr(self, run, extract):
        self.assertIsNone(tp.download_youtube_audio(URL))
        extract.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
