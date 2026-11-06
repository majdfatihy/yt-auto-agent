"""One-time surgical patch: ONLY connect proofreading inside preview in agent.py."""
from pathlib import Path

p = Path(__file__).resolve().parent / 'agent.py'
if not p.is_file():
    raise SystemExit('ERROR: Put this script in repository root, next to agent.py.')
source = p.read_text(encoding='utf-8')
import_line = 'from transcript_proofreader import proofread_timed_cues\n'
needle_import = 'from caption_revisions import apply_approved_edits\n'
needle_call = "                    preview_cues = apply_approved_edits(preview_cues, VIDEO_URL)"
replacement_call = ("                    # Audio-grounded proofread: Gemini -> Deepgram -> AssemblyAI.\n"
                    "                    preview_cues = proofread_timed_cues(preview_cues)\n"
                    "                    # Explicit timestamp-specific edits take final precedence.\n"
                    "                    preview_cues = apply_approved_edits(preview_cues, VIDEO_URL)")
if import_line in source and replacement_call in source:
    print('ALREADY_APPLIED: No files changed.')
    raise SystemExit(0)
if source.count(needle_import) != 1 or source.count(needle_call) != 1 or import_line in source:
    raise SystemExit('REFUSED: Expected preview anchors not found exactly once. No files changed.')
new = source.replace(needle_import, needle_import + import_line, 1)
new = new.replace(needle_call, replacement_call, 1)
p.write_text(new, encoding='utf-8')
print('PATCH_APPLIED: agent.py: added 1 import, 1 preview-only proofread call; no other edits.')
