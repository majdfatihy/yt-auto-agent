import hashlib
import io
import json
import os
import sys
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from approved_publish import download_approved_previews, publish_approved_clips

REF = '2026-11-04T07:13:07.382901+00:00'
SOURCE = 'https://youtu.be/wQUPA9K2MSY'
DATA = b'\0\0\0\x18ftypmp42' + b'a' * 15000

def plan():
    return dict(status='publish_queued', created_utc=REF, video_url=SOURCE,
                telegram_file_id='abc', preview_run_id='12345', previewed_ids=[1],
                approved_ids=[1], approval_utc='2026-11-06T08:00:00Z',
                clips=[dict(id=1, title='Clip One', start_time=0, end_time=30)])

def archive_bytes(bad_sha=False):
    manifest=dict(plan_ref=REF, video_url=SOURCE, telegram_file_id='abc',
                  preview_run_id='12345',previewed_ids=[1],files={'1': '0' * 64 if bad_sha else hashlib.sha256(DATA).hexdigest()})
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:
        z.writestr('clip-1.mp4',DATA)
        z.writestr('manifest.json',json.dumps(manifest))
    return f.getvalue()

class MockReply:
    def __init__(self, content=None, value=None):self.content=content;self.value=value
    def raise_for_status(self):pass
    def json(self):return self.value

class FakeHttp:
    def __init__(self, bad_sha=False):self.bad_sha=bad_sha
    def get(self,url,**kw):
        if url.endswith('/artifacts'):
            return MockReply(value={'artifacts':[{'name':'preview-12345','expired':False,'id':123,'size_in_bytes':30000}]})
        return MockReply(content=archive_bytes(self.bad_sha))

class ApprovalTests(unittest.TestCase):
    def test_download_validated_exact_video(self):
        with patch.dict(os.environ,{'GH_PAT':'fake','GITHUB_REPOSITORY':'owner/repo'}):
            self.assertEqual(download_approved_previews(plan(),REF,[1],FakeHttp()),{1:DATA})

    def test_reject_wrong_hash(self):
        with patch.dict(os.environ,{'GH_PAT':'fake','GITHUB_REPOSITORY':'owner/repo'}):
            with self.assertRaisesRegex(RuntimeError,'SHA256'):
                download_approved_previews(plan(),REF,[1],FakeHttp(True))

    def test_reject_unapproved_and_replay(self):
        for status in ('previewed_awaiting_approval','approved','publishing','published'):
            p=plan();p['status']=status
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError,'NOT_APPROVED'):
                publish_approved_clips(REF,'1',SOURCE,'abc',lambda:p,lambda _:None,
                                        lambda *_:'✅ uploaded',lambda _:None)

    def test_reject_other_source_and_wrong_ids(self):
        p=plan()
        for ref,chosen,source,fileid in ((REF,'1','https://youtu.be/other','abc'),
                                         (REF,'2',SOURCE,'abc'),
                                         ('wrong','1',SOURCE,'abc'),
                                         (REF,'1',SOURCE,'wrong')):
            with self.subTest(ref=ref,chosen=chosen,source=source),self.assertRaises(RuntimeError):
                publish_approved_clips(ref,chosen,source,fileid,lambda:p,lambda _:None,
                                        lambda *_:'✅ uploaded',lambda _:None)

    def test_success_changes_state_and_prevents_retry(self):
        current=plan(); states=[]; uploaded=[]; notifications=[]
        def read():return deepcopy(current)
        def save(new): current.clear();current.update(deepcopy(new));states.append(current['status'])
        def upload(path,title,desc,tags):
            uploaded.append((Path(path).read_bytes(),title))
            return '✅ يوتيوب: تم النشر بنجاح! (https://youtu.be/mock)'
        with patch.dict(os.environ,{'YT_CLIENT_ID':'a','YT_CLIENT_SECRET':'b','YT_REFRESH_TOKEN':'c'}):
            results=publish_approved_clips(REF,'1',SOURCE,'abc',read,save,upload,
                notifications.append,downloader=lambda *args:{1:DATA})
        self.assertEqual(uploaded,[(DATA,'Clip One')]);self.assertEqual(current['status'],'published')
        self.assertIn('publishing',states);self.assertEqual(results[0]['clip_id'],1)
        with self.assertRaisesRegex(RuntimeError,'NOT_APPROVED'):
            publish_approved_clips(REF,'1',SOURCE,'abc',read,save,upload,notifications.append)
        self.assertEqual(len(uploaded),1)

    def test_missing_credentials_does_not_reserve_or_upload(self):
        p=plan()
        with patch.dict(os.environ,{'YT_CLIENT_ID':'','YT_CLIENT_SECRET':'','YT_REFRESH_TOKEN':''}):
            with self.assertRaisesRegex(RuntimeError,'OAUTH_MISSING'):
                publish_approved_clips(REF,'1',SOURCE,'abc',lambda:p,lambda _:None,
                    lambda *_:self.fail('unexpected upload'),lambda _:None,downloader=lambda *a:{1:DATA})
        self.assertEqual(p['status'],'publish_queued')

    def test_failed_upload_stays_publishing_to_prevent_duplicates(self):
        current=plan()
        def save(new): current.clear(); current.update(deepcopy(new))
        with patch.dict(os.environ,{'YT_CLIENT_ID':'a','YT_CLIENT_SECRET':'b','YT_REFRESH_TOKEN':'c'}):
            with self.assertRaisesRegex(RuntimeError,'UPLOAD_UNCONFIRMED'):
                publish_approved_clips(REF,'1',SOURCE,'abc',lambda:deepcopy(current),save,
                    lambda *_:'❌ upload failed',lambda _:None,downloader=lambda *a:{1:DATA})
        self.assertEqual(current['status'],'publishing')

if __name__=='__main__':unittest.main()
