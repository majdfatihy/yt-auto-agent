import unittest
from reel_full_plan import build_remaining_plan, remaining_intervals, divide_gap
from reel_history import CONFIRMED_LEGACY_UPLOADS, canonical_source, overlaps

SOURCE='https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view'
CUES=[{'start':i*3.0,'end':i*3.0+2.7,
       'text':f'شرح كيفية استقبال الحوالات الدولية وتفاصيل فودافون كاش رقم {i}'}
      for i in range(80)]

class FullVideoTest(unittest.TestCase):
    def test_published_window_excluded_and_remaining_covers_video(self):
        history=CONFIRMED_LEGACY_UPLOADS[canonical_source(SOURCE)]
        plan=build_remaining_plan(SOURCE,'',CUES,210.0,history)
        self.assertEqual(plan['version'],2)
        self.assertEqual(plan['previously_uploaded'],1)
        self.assertGreaterEqual(len(plan['clips']),4)
        for clip in plan['clips']:
            a,b=clip['start_time'],clip['end_time']
            self.assertTrue(14.95 <= b-a <=59.05)
            self.assertFalse(overlaps(a,b,66.5,106.4))
            self.assertGreaterEqual(clip['serial'],2)
        for x,y in zip(plan['clips'],plan['clips'][1:]):
            self.assertLessEqual(x['end_time'],y['start_time']+0.01)
        self.assertEqual(plan['clips'][0]['start_time'],0.0)
        self.assertAlmostEqual(plan['clips'][-1]['end_time'],210.0,places=1)
        self.assertTrue(any(c['start_time']>=106.4 for c in plan['clips']))
        self.assertTrue(any(c['end_time']<=66.5 for c in plan['clips']))

    def test_no_remaining_after_all_published(self):
        history=[{'youtube_video_id':'abcdefghijk','start_time':0,'end_time':60}]
        plan=build_remaining_plan(SOURCE,'',CUES,60.0,history)
        self.assertEqual(plan['status'],'all_published_or_no_speech')
        self.assertEqual(plan['clips'],[])

    def test_published_windows_merge(self):
        gaps=remaining_intervals(220,[(25,55),(54.9,90),(135,165)])
        self.assertEqual(gaps,[(90,135),(165,220)])

if __name__=='__main__': unittest.main()
