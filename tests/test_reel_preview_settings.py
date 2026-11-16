"""Focused offline tests for preview/rendering parity. No external credentials."""
import unittest

from reel_preview_settings import resolve_preview_settings


class PreviewSettingsTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"clip_settings": {
            "1": {"captions": False, "blur": "off", "privacy": "unlisted"},
            "2": {"blur": "on"}
        }}

    def test_legacy_payload_uses_per_clip_and_sane_defaults(self):
        actual = resolve_preview_settings(self.plan, [1, 2], "")
        self.assertEqual(actual[1], {
            "captions": False, "blur": "off", "privacy": "unlisted"})
        self.assertEqual(actual[2], {
            "captions": True, "blur": "on", "privacy": "public"})

    def test_worker_payload_overrides_kv_defaults_and_per_clip(self):
        value = ('{"1":{"captions":true,"blur":"on","privacy":"private"},'
                 '"2":{"captions":false,"blur":"off","privacy":"unlisted"}}')
        actual = resolve_preview_settings(self.plan, [1, 2], value)
        self.assertEqual(actual[1]["privacy"], "private")
        self.assertEqual(actual[1]["captions"], True)
        self.assertEqual(actual[2]["captions"], False)
        self.assertEqual(actual[2]["privacy"], "unlisted")
        self.assertEqual(actual[2]["blur"], "off")

    def test_missing_or_extra_ids_are_rejected(self):
        for raw in ('{"1":{"captions":false}}',
                    '{"1":{},"2":{},"3":{}}'):
            with self.assertRaisesRegex(ValueError, "IDS_MISMATCH"):
                resolve_preview_settings(self.plan, [1, 2], raw)

    def test_invalid_payload_cannot_silently_publish_unexpected_privacy(self):
        for raw in ('not json', "[]",
                    '{"1":{"privacy":"unknown"}}',
                    '{"1":{"captions":"off"}}',
                    '{"1":{"blur":"soft"}}',
                    '{"1":{"privacy":"public","unsafe":true}}'):
            with self.assertRaises(ValueError):
                resolve_preview_settings(self.plan, [1], raw)

    def test_original_configuration_is_not_mutated(self):
        original = dict(self.plan["clip_settings"]["1"])
        resolve_preview_settings(self.plan, [1],
            '{"1":{"captions":true,"blur":"on","privacy":"public"}}')
        self.assertEqual(original, self.plan["clip_settings"]["1"])


if __name__ == "__main__":
    unittest.main()
