import unittest
from telegram_queue_ui import dashboard, parse_callback

class TelegramQueueUiTests(unittest.TestCase):
    def test_empty_dashboard(self):
        result=dashboard([])
        self.assertIn("الإجمالي: 0",result["text"])
        self.assertEqual(len(result["reply_markup"]["inline_keyboard"]),3)
    def test_pagination(self):
        rows=[{"task_id":str(i),"state":"planned"} for i in range(12)]
        result=dashboard(rows,page=1)
        self.assertIn("عرض 6–10 من 12",result["text"])
        self.assertIn("queue:list:2",str(result["reply_markup"]))
    def test_filter(self):
        rows=[{"task_id":"1","state":"scheduled"},{"task_id":"2","state":"invalid"}]
        result=dashboard(rows,"queue:failed")
        self.assertIn("2 — invalid",result["text"])
        self.assertNotIn("1 — scheduled",result["text"])
    def test_callback(self):
        self.assertEqual(parse_callback("queue:scheduled:3"),("queue:scheduled",3))
    def test_reject_publish_callback(self):
        with self.assertRaises(ValueError):parse_callback("queue:publish:3")
    def test_reject_negative_page(self):
        with self.assertRaises(ValueError):dashboard([],page=-1)

if __name__=="__main__":
    unittest.main()
