import unittest
from telegram_reel_details import format_reel_details

class CollapsibleMetadataTests(unittest.TestCase):
    def test_telegram_native_expandable_quote_hides_details_by_default(self):
        clip={"id":3,"serial":3}
        seo={"title":"خطوات استلام الحوالات الدولية #فودافون_كاش 3",
             "description":"طريقة شحن المحفظة\nالحد اليومي للتحويل\nملاحظات مهمة\n#فودافون_كاش #حوالات",
             "tags_string":"فودافون كاش,الحوالات الدولية,تحويل الاموال"}
        result=format_reel_details(clip,seo,{},lambda facts:"لغة الفيديو: العربية")
        self.assertIn("<blockquote expandable>", result)
        self.assertIn("</blockquote>",result)
        self.assertIn("ريلز 3 — بيانات النشر",result)
        self.assertIn("كلمات يوتيوب",result)
        self.assertIn("لغة الفيديو: العربية",result)
        self.assertLess(len(result),4000)
        self.assertTrue(result.index("<blockquote expandable>") > result.index("العنوان"))

    def test_escape_untrusted_html_and_metadata(self):
        clip={"id":1}
        seo={"title":"شرح عنوان <script>alert(1)</script> #هاشتاغ 1",
             "description":"شرح <b>غير موثوق</b>",
             "tags_string":"a&b,شرح"}
        result=format_reel_details(clip,seo,{},lambda facts:"تاريخ <غير معروف> & مؤكد")
        self.assertIn("&lt;script&gt;",result)
        self.assertNotIn("<script>",result)
        self.assertIn("a&amp;b",result)
        self.assertIn("&lt;غير معروف&gt;",result)

    def test_reject_overlong_message_instead_of_splitting_html(self):
        with self.assertRaisesRegex(RuntimeError, "TOO_LONG"):
            format_reel_details({"id":1},
                {"title":"عنوان مقطع مناسب","description":"ك"*3900,"tags_string":"tag"},
                {},lambda facts:"لغة عربية")

if __name__=="__main__":
    unittest.main()
