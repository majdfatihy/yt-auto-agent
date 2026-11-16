import unittest
from unittest.mock import patch

from reel_metadata import (
    clip_transcript, full_video_context, parse_youtube_tags,
    youtube_tag_cost, generate_reel_metadata, numbered_seo_title, _validate,
)

CUES = [
    {'start': 0.0, 'end': 6.0, 'text': 'شرحنا في الفيديو طريقة شحن كارت تلدا باستخدام إنستا باي'},
    {'start': 6.0, 'end': 13.0, 'text': 'وبعدها بنختار البطاقة الإلكترونية ونكتب الرقم بشكل صحيح'},
    {'start': 13.0, 'end': 18.0, 'text': 'وفي الآخر بنتأكد إن التحويل تم بنجاح'},
    {'start': 30.0, 'end': 40.0, 'text': 'ده موضوع تاني ومينفعش يظهر في وصف المقطع الأول'}
]

SEO = {
    'title': 'طريقة شحن كارت تلدا باستخدام إنستا باي',
    'long_titles': [
        'شرح طريقة شحن كارت تلدا باستخدام إنستا باي خطوة بخطوة',
        'كيفية اختيار البطاقة الإلكترونية عند شحن كارت تلدا',
        'خطوات شحن كارت تلدا والتأكد من إتمام التحويل',
    ],
    'short_titles': [
        'شحن كارت تلدا من إنستا باي',
        'طريقة اختيار البطاقة الإلكترونية في إنستا باي',
        'خطوات التأكد من تحويل رصيد تلدا',
    ],
    'description_lines': [
        'تعرف على طريقة شحن كارت تلدا باستخدام خدمة إنستا باي.',
        'نوضح اختيار البطاقة الإلكترونية وكتابة البيانات بشكل صحيح.',
        'نشرح كيفية التأكد من إتمام عملية التحويل بنجاح.',
    ],
    'hashtags': ['#تلدا', '#إنستا_باي', '#شورتس'],
    'keywords': [
        'شحن تلدا', 'كارت تلدا', 'شحن كارت تلدا من إنستا باي',
        'البطاقة الإلكترونية', 'تحويل رصيد تلدا', 'طريقة شحن تلدا',
        'إنستا باي وتلدا', 'التأكد من التحويل',
    ],
}

class ReelMetadataTests(unittest.TestCase):
    def test_only_clip_text_is_used_for_claims(self):
        text = clip_transcript(CUES, 0, 18)
        self.assertIn('تلدا', text)
        self.assertNotIn('موضوع تاني', text)

    def test_full_video_context_covers_selected_and_later_topics(self):
        text = full_video_context(CUES)
        self.assertIn('تلدا', text)
        self.assertIn('موضوع تاني', text)

    def test_model_receives_full_video_context_and_exact_clip(self):
        prompts = []
        def fake_generate(prompt, key, env, validator):
            prompts.append(prompt)
            return validator(SEO), 'groq:openai/gpt-oss-120b'
        with patch('reel_metadata.generate_json', side_effect=fake_generate):
            result = generate_reel_metadata(CUES, 0, 18, 'مقتطف: باي', '', clip_number=2)
        self.assertIn('موضوع تاني', prompts[0])
        self.assertIn('الكلام الحقيقي داخل الريلز المختار فقط', prompts[0])
        self.assertEqual(result['method'], 'groq:openai/gpt-oss-120b')
        self.assertEqual(len(result['description'].splitlines()), 4)
        self.assertTrue(result['title'].endswith(' #تلدا 2'))
        self.assertLessEqual(len(result['title']), 100)
        self.assertNotIn('long_titles', result)
        self.assertIn('#تلدا', result['description'])
        self.assertGreaterEqual(len(parse_youtube_tags(result['tags_string'])), 6)
        self.assertLessEqual(youtube_tag_cost(parse_youtube_tags(result['tags_string'])), 500)

    def test_long_title_truncated_by_words_before_hashtag_and_number(self):
        long_title = 'طريقة شحن كارت تلدا باستخدام إنستا باي ' * 5
        result = numbered_seo_title(long_title, ['#تلدا', '#شورتس'], 17,
            clip_transcript(CUES, 0, 18))
        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.endswith('#تلدا 17'))

    def test_tags_include_multiword_quote_cost(self):
        self.assertEqual(youtube_tag_cost(['تحويل دولي', 'فودافون']), 10 + 2 + 1 + 7)
        with self.assertRaisesRegex(ValueError, '500'):
            parse_youtube_tags(','.join(f'شحن كارت تلدا والتطبيق {i:02d}' for i in range(18)))

    def test_no_literal_transcript_fallback_if_ai_is_unavailable(self):
        with patch('reel_metadata.generate_json', side_effect=RuntimeError('API unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'REEL_SEO_UNAVAILABLE'):
                generate_reel_metadata(CUES, 0, 18, 'مقتطف: مرحبا يا شباب', '')

    def test_reject_unrelated_title(self):
        wrong = {**SEO, 'title': 'كيفية تحديث نظام ويندوز في الكمبيوتر'}
        with self.assertRaisesRegex(ValueError, 'TITLE'):
            _validate(wrong, clip_transcript(CUES, 0, 18), full_video_context(CUES))

    def test_reject_description_link(self):
        wrong = {**SEO, 'description_lines': [
            'يمكن شحن تلدا عن طريق إنستا باي',
            'استخدم الرابط https://youtu.be/abc',
            'راجع تفاصيل التحويل في التطبيق',
        ]}
        with self.assertRaisesRegex(ValueError, 'DESCRIPTION_INVALID'):
            _validate(wrong)

    def test_no_dates_are_fabricated(self):
        result = _validate(SEO, clip_transcript(CUES, 0, 18), full_video_context(CUES))
        self.assertNotIn('recording_date', result)
        self.assertNotIn('published_at', result)

if __name__ == '__main__':
    unittest.main()
