import unittest
from unittest.mock import patch

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

    def test_ai_metadata_three_lines_with_hashtags(self):
        response = {
            'title': 'طريقة شحن كارت تلدا باستخدام إنستا باي',
            'description': 'شرح شحن تلدا\\nاختيار البطاقة الإلكترونية\\nالتأكد من التحويل\\n#تلدا #شرح #شورتس'
        }
        with patch('reel_metadata.generate_json', return_value=(response, 'groq:openai/gpt-oss-120b')):
            result = generate_reel_metadata(
                CUES, 0, 18, 'طريقة شحن تلدا من إنستا باي', '')
        self.assertEqual(result['method'], 'groq:openai/gpt-oss-120b')
        self.assertLessEqual(len(result['title']), 100)
        self.assertEqual(len(result['description'].splitlines()), 4)
        self.assertIn('#تلدا', result['description'])
        self.assertNotIn('youtu.be', result['description'])

    def test_no_raw_title_fallback_if_ai_unavailable(self):
        with patch('reel_metadata.generate_json', side_effect=RuntimeError('API unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'REEL_METADATA_NOT_READY'):
                generate_reel_metadata(CUES, 0, 18, 'مقتطف: مرحبا يا شباب', '')

    def test_unrelated_title_rejected_even_with_structured_json(self):
        payload = {'title': 'كيفية تحديث نظام ويندوز في الكمبيوتر',
                   'description_lines': [
                       'شرح تحديث ويندوز', 'حل مشكلة الكمبيوتر', 'إعدادات نظام التشغيل'],
                   'hashtags': ['#ويندوز', '#كمبيوتر', '#شرح']}
        with patch('reel_metadata.generate_json', side_effect=lambda prompt, key, env, validator:
                   (validator(payload), 'gemini:gemini-3.8-flash')):
            with self.assertRaisesRegex(ValueError, 'NOT_GROUNDED'):
                generate_reel_metadata(CUES, 0, 18, 'شحن تلدا', '')

    def test_reject_link_and_invalid_hashtags(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_DESCRIPTION_TEXT'):
            _validate({'title': 'طريقة شحن كارت تلدا', 'description_lines': [
                'خطوات شحن كارت تلدا', 'رابط https://youtu.be/abc', 'فحص التحويل الآن'
            ], 'hashtags': ['#تلدا', '#انستاباي', '#شورتس']})
        with self.assertRaisesRegex(ValueError, 'INVALID_HASHTAG_FORMAT'):
            _validate({'title': 'طريقة شحن كارت تلدا', 'description_lines': [
                'خطوات شحن كارت تلدا', 'طريقة استخدام انستا باي', 'فحص التحويل بشكل صحيح'
            ], 'hashtags': ['#تلدا', '#غير صالح', '#شورتس']})


if __name__ == '__main__':
    unittest.main()
