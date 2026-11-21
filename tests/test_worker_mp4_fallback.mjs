import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker = (await import(pathToFileURL(new URL('../worker.js', import.meta.url).pathname))).default;

const kv = new Map(), sent = [], dispatches = [];
const env = {
  TELEGRAM_TOKEN: 'test-token', TELEGRAM_CHAT_ID: '999',
  GITHUB_PAT: 'test-key', GITHUB_REPO: 'example/repo',
  TELEGRAM_WEBHOOK_SECRET: 'test-secret',
  BOT_STATE: {
    get: async key => kv.get(key) || null,
    put: async (key, value) => { kv.set(key, value); }
  }
};
globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  if (url.startsWith('https://api.telegram.org/')) {
    sent.push({method: url.split('/').at(-1), payload: JSON.parse(opts.body)});
    return Response.json({ok:true,result:true});
  }
  if (url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS'))
    return new Response(null, {status:404});
  if (url.endsWith('/dispatches')) {
    dispatches.push(JSON.parse(opts.body));
    return new Response(null, {status:204});
  }
  throw Error('Unexpected network access: ' + url);
};
let updateId=700;
async function sendMessage(message) {
  const result = await worker.fetch(new Request('https://test.example/', {
    method:'POST',
    headers: {'content-type':'application/json',
      'X-Telegram-Bot-Api-Secret-Token':'test-secret'},
    body:JSON.stringify({update_id:updateId++,message:{chat:{id:999},...message}})
  }), env);
  assert.equal(result.status,200);
}
const url='https://youtu.be/DggHblFwsC4';
await sendMessage({text:url});
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.video_url,url);
assert.equal(dispatches[0].client_payload.agent_action,'prepare');
await sendMessage({document:{file_id:'telegram-test-file-id',
  file_name:'fallback.mp4',mime_type:'video/mp4',file_size:12*1024*1024}});
assert.equal(dispatches.length,2,'attached MP4 must automatically retry preparation');
const payload=dispatches[1].client_payload;
assert.equal(payload.video_url,url,'retain the saved original YouTube link');
assert.equal(payload.telegram_file_id,'telegram-test-file-id');
assert.equal(payload.agent_action,'prepare');
assert.equal(JSON.parse(kv.get('source:999')).telegram_file_id,'telegram-test-file-id');
assert.match(sent.filter(s=>s.method==='sendMessage').at(-1).payload.text,/بدون تنزيل من يوتيوب/);
await sendMessage({document:{file_id:'too-big',file_name:'huge.mp4',file_size:21*1024*1024}});
assert.equal(dispatches.length,2,'oversized MP4 must not start any job');
assert.match(sent.filter(s=>s.method==='sendMessage').at(-1).payload.text,/أكبر من 20MB/);
assert.ok(dispatches.every(x=>x.client_payload.agent_action==='prepare'),
  'fallback must never publish without owner approval');
console.log('Telegram MP4 fallback: saved-link reuse, automatic prepare, size limit, and no auto-publish passed');
