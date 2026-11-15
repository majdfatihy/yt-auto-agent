import unittest

from reel_metadata import clip_transcript, generate_reel_metadata, _validate

CUES = [
    {'start': 0.0, 'end': 6.0, 'text': 'شرحنا في الفيديو طريقة شحن كارت تلدا باستخدام إنستا باي'},
    {'start': 6.0, 'end': 13.0, 'text': 'وبعدها بنختار البطاقة الإلكترونية ونكتب الرقم بشكل صحيح'},
    {'start': 13.0, 'end': 18.0, 'text': 'وفي الآخر بنتأكد إن التحويل تم بنجاح'},
    {'start': 30.0, 'end': 40.0, 'text': 'ده موضوع تاني ومينفعش يظهر في وصف المقطع الأول'}
]


class ReelMetadataTests(unittest.TestCase):
    def test_context_is_only_selected_clip(self):
        text = clip_transcript(CUES, 0, 18)
        self.assertIn('تلدا', text)
        self.assertNotIn('موضوع تاني', text)

    def test_fallback_has_three_lines_and_topic_hashtags(self):
        result = generate_reel_metadata(CUES, 0, 18, 'شحن كارت تلدا من إنستا باي', '')
        self.assertEqual(result['method'], 'extractive_fallback')
        self.assertLessEqual(len(result['title']), 100)
        self.assertEqual(len(result['description'].splitlines()), 4)
        self.assertIn('#', result['description'])
        self.assertNotIn('https://', result['description'])
        self.assertNotIn('youtu.be', result['description'])

    def test_reject_link_and_invalid_hashtags(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_DESCRIPTION_TEXT'):
            _validate({'title': 'شرح شحن تلدا', 'description_lines': [
                'خطوات شحن كارت تلدا', 'رابط https://youtu.be/abc', 'فحص التحويل الآن'
            ], 'hashtags': ['#تلدا', '#انستاباي', '#شورتس']})
        with self.assertRaisesRegex(ValueError, 'INVALID_HASHTAG_FORMAT'):
            _validate({'title': 'شرح شحن تلدا', 'description_lines': [
                'خطوات شحن كارت تلدا', 'طريقة استخدام انستا باي', 'فحص التحويل بشكل صحيح'
            ], 'hashtags': ['#تلدا', '#غير صالح', '#شورتس']})


if __name__ == '__main__':
    unittest.main()
