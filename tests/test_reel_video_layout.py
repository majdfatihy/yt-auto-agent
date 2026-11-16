"""Source orientation/ratio-based blur bypass tests; no media uploads."""
import unittest
from reel_video_layout import display_dimensions, layout_filter


class SourceLayoutTests(unittest.TestCase):
    def test_native_1080p_vertical_has_no_scale_or_blur(self):
        w, h, aspect=display_dimensions({"streams":[{
            "width":1080,"height":1920,"sample_aspect_ratio":"1:1"}]})
        filt, mode=layout_filter(w,h,aspect)
        self.assertEqual(mode, "native_vertical_no_blur")
        self.assertNotIn("boxblur",filt)
        self.assertNotIn("scale=",filt)

    def test_720x1280_vertical_only_scales(self):
        w,h,aspect=display_dimensions({"streams":[{
            "width":720,"height":1280}]})
        filt, mode=layout_filter(w,h,aspect)
        self.assertEqual(mode, "native_vertical_no_blur")
        self.assertIn("scale=1080:1920",filt)
        self.assertNotIn("boxblur",filt)

    def test_rotation_90_becomes_portrait_and_skips_blur(self):
        w,h,aspect=display_dimensions({"streams":[{
            "width":1920,"height":1080,"sample_aspect_ratio":"1:1",
            "side_data_list":[{"rotation":90}]}]})
        self.assertEqual((w,h),(1080,1920))
        self.assertEqual(layout_filter(w,h,aspect)[1],"native_vertical_no_blur")

    def test_landscape_requires_blurred_background(self):
        w,h,aspect=display_dimensions({"streams":[{
            "width":1920,"height":1080}]})
        filt, mode=layout_filter(w,h,aspect)
        self.assertEqual(mode, "letterbox_blurred_background")
        self.assertIn("boxblur",filt)
        self.assertIn("force_original_aspect_ratio=decrease",filt)

    def test_near_vertical_4_by_5_is_not_confused_with_reels(self):
        w,h,aspect=display_dimensions({"streams":[{
            "width":1080,"height":1350}]})
        self.assertEqual(layout_filter(w,h,aspect)[1],
                         "letterbox_blurred_background")

    def test_bad_dimensions_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"INVALID"):
            display_dimensions({"streams":[{"width":0,"height":1920}]})

if __name__=="__main__":
    unittest.main()
