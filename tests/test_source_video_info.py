import unittest
from unittest.mock import Mock
from source_video_info import read_source_facts, preview_fact_lines, original_youtube_id

class SourceFactTests(unittest.TestCase):
    def test_google_drive_never_implies_youtube_upload_or_recording_date(self):
        facts = read_source_facts(
            'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',
            'شرح الحوالات الدولية على فودافون كاش وخطوات اختيار الدولة')
        self.assertEqual(facts['source_platform'], 'Google Drive / MP4')
        self.assertIsNone(facts['source_published_at'])
        self.assertIsNone(facts['source_recorded_at'])
        self.assertEqual(facts['speech_language_inferred'], 'ar')
        self.assertIn('غير متاح/غير موثق', preview_fact_lines(facts))

    def test_official_youtube_snippet_and_recording_date_are_separate(self):
        client = Mock()
        client.videos.return_value.list.return_value.execute.return_value = {
            'items':[{
                'snippet':{'title':'عنوان الفيديو المصدر', 'publishedAt':'2026-08-01T13:00:00Z',
                           'defaultAudioLanguage':'ar'},
                'recordingDetails':{'recordingDate':'2026-07-30T09:00:00Z'}
            }]
        }
        facts = read_source_facts('https://youtu.be/abcdefghijk',
                                  'شرح فيديو عربي', client)
        self.assertEqual(facts['source_published_at'], '2026-08-01T13:00:00Z')
        self.assertEqual(facts['source_recorded_at'], '2026-07-30T09:00:00Z')
        self.assertEqual(facts['source_audio_language'], 'ar')
        client.videos.return_value.list.assert_called_once_with(
            part='snippet,recordingDetails', id='abcdefghijk')

    def test_youtube_identifier_strict(self):
        self.assertEqual(original_youtube_id('https://youtube.com/watch?v=abcdefghijk'),
                         'abcdefghijk')
        self.assertIsNone(original_youtube_id('https://notyoutube.com/watch?v=abcdefghijk'))

if __name__ == '__main__':
    unittest.main()
