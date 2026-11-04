// Completely mocked Cloudflare Worker smoke tests. No network.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const worker = (await import(pathToFileURL(new URL('../worker.js', import.meta.url).pathname))).default;
const sent = [];
const dispatches = [];
const patches = [];
const kv = new Map();
const plan = {
  version: 1, status: 'awaiting_selection',
  created_utc: '2026-11-04T07:13:07.382901+00:00',
  video_url: 'https://youtu.be/wQUPA9K2MSY', telegram_file_id: 'abc',
  duration: 92.3,
  clips: [
    {id: 1, title: 'First', start_time: 0, end_time: 30},
    {id: 2, title: 'Second', start_time: 30, end_time: 55},
    {id: 3, title: 'Third', start_time: 55, end_time: 82},
  ]
};
let savedPlan = plan;
globalThis.fetch = async (url, opts={}) => {
  if (url.startsWith('https://api.telegram.org/')) {
    sent.push(JSON.parse(opts.body));
    return Response.json({ok:true, result:true});
  }
  if (url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')) {
    if (opts.method === 'PATCH') {
      patches.push(JSON.parse(opts.body));
      savedPlan = {};
      return new Response(null, {status:204});
    }
    return Response.json({value: JSON.stringify(savedPlan)});
  }
  if (url.endsWith('/dispatches')) {
    dispatches.push(JSON.parse(opts.body));
    return new Response(null, {status:204});
  }
  throw Error('Unexpected URL ' + url);
};
const env = {
  TELEGRAM_TOKEN:'mock-token', TELEGRAM_CHAT_ID:'999', GITHUB_PAT:'mock-github-token',
  TELEGRAM_WEBHOOK_SECRET:'secret', GITHUB_REPO:'owner/repo',
  BOT_STATE:{get: async k=>kv.get(k)||null, put: async (k,v)=>{kv.set(k,v);}}
};
kv.set('source:999', JSON.stringify({video_url: plan.video_url, telegram_file_id: 'abc'}));
let updateId=100;
async function message(text) {
  const input = new Request('https://worker.example', {
    method: 'POST', headers: {'X-Telegram-Bot-Api-Secret-Token':'secret',
                             'content-type':'application/json'},
    body:JSON.stringify({update_id:updateId++,message:{chat:{id:999},text}})
  });
  const response = await worker.fetch(input,env);
  assert.equal(response.status, 200);
}
await message('١،٣');
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.agent_action,'preview');
assert.equal(dispatches[0].client_payload.clip_ids,'1,3');
assert.equal(dispatches[0].client_payload.clip_plan_ref,plan.created_utc);
await message('/clip 2');
assert.equal(dispatches.length,2);
assert.equal(dispatches[1].client_payload.clip_ids,'2');
await message('/publish');
assert.equal(dispatches.length,2,'publish must not dispatch');
await message('/clip 8');
assert.equal(dispatches.length,2,'invalid selection must not dispatch');
await message('/cancel');
assert.equal(patches.length,1,'cancel invalidates plan');
await message('1');
assert.equal(dispatches.length,2,'after cancel, no clip dispatch');
console.log('Worker offline tests passed: 6 scenarios');
