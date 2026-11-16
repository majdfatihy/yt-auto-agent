"""Isolated test: do not give a new callback to an old deployed Worker."""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'agent.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
node = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
            and item.name == '_preview_keyboard')
namespace = {'os': os, '_ui_fingerprint': lambda plan: '123abc'}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
make_button = namespace['_preview_keyboard']
PLAN = {'clips': [{'id': 1}], 'created_utc': '2026-11-16T00:00:00Z'}

class PreviewButtonRolloutTests(unittest.TestCase):
    def test_approval_always_dispatches_one_tap_publishing(self):
        # Production Cloudflare Worker supports direct approval. A stale GitHub
        # flag must not force the old two-step 'approve then confirm' flow.
        for enabled in ('true', 'false', ''):
            with self.subTest(enabled=enabled), patch.dict(os.environ, {
                    'TELEGRAM_ONE_TAP_ENABLED': enabled,
                    'GITHUB_RUN_ID': '34567'}):
                result = make_button(PLAN, [1])
            action = result['inline_keyboard'][0][0]['callback_data']
            self.assertEqual(action, 'p:approvepublish:123abc:34567')
            self.assertIn('اعتماد ونشر', result['inline_keyboard'][0][0]['text'])

if __name__ == '__main__':
    unittest.main()
