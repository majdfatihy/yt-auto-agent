import unittest
from datetime import datetime, timezone
from reel_schedule import schedule_utc, validate_recording_date

NOW=datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)

class ScheduleTests(unittest.TestCase):
    def test_no_schedule_stays_private(self):
        self.assertIsNone(schedule_utc({}, 0, 1, now=NOW))

    def test_cairo_time_converted_and_multiple_hours_spaced(self):
        plan={'schedule':{'first_local':'2026-11-20 18:00','spacing_hours':24}}
        a=schedule_utc(plan,0,3,now=NOW)
        b=schedule_utc(plan,1,3,now=NOW)
        self.assertEqual(a,'2026-11-20T16:00:00Z')
        self.assertEqual(b,'2026-11-21T16:00:00Z')

    def test_multi_requires_explicit_spacing(self):
        with self.assertRaisesRegex(ValueError,'SPACING'):
            schedule_utc({'schedule':{'first_local':'2026-11-20 18:00'}},0,3,now=NOW)

    def test_never_schedule_in_past(self):
        with self.assertRaisesRegex(ValueError,'FUTURE'):
            schedule_utc({'schedule':{'first_local':'2020-01-01 12:00'}},0,1,now=NOW)

    def test_recorded_date_requires_real_timezone_and_is_not_fabricated(self):
        self.assertIsNone(validate_recording_date(None,now=NOW))
        with self.assertRaisesRegex(ValueError,'NEEDS_TIMEZONE'):
            validate_recording_date('2026-11-15T14:30',now=NOW)
        self.assertEqual(
            validate_recording_date('2026-11-15T14:30:00+02:00',now=NOW),
            '2026-11-15T12:30:00Z')

if __name__=='__main__':
    unittest.main()
