import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const worker = (await import(pathToFileURL(new URL('../worker.js',import.meta.url).pathname))).default;
const source = {video_url:'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',telegram_file_id:''};
const initial = {
  version:1, status:'awaiting_selection',
  created_utc:'2026-11-16T00:42:00.000001+00:00', ...source, duration:120,
  clips:[
    {id:1,title:'طريقة الإيداع',start_time:0,end_time:35},
    {id:2,title:'التحويل',start_time:40,end_time:80},
    {id:3,title:'إعداد الحساب',start_time:81,end_time:119}
  ]
};
let plan = structuredClone(initial), sent=[], dispatched=[], artifact=true;
const kv = new Map([['source:999',JSON.stringify(source)]]);
globalThis.fetch = async(url,opts={}) => {
  if(url.startsWith('https://api.telegram.org/')) {
    sent.push({method:url.split('/').at(-1),data:JSON.parse(opts.body)});
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')) {
    if(opts.method === 'PATCH') {plan=JSON.parse(JSON.parse(opts.body).value);return new Response(null,{status:204});}
    return Response.json({value:JSON.stringify(plan)});
  }
  if(url.endsWith('/actions/runs/13579/artifacts')) {
    return Response.json({artifacts:artifact?[{name:'preview-13579',expired:false}]:[]});
  }
  if(url.endsWith('/dispatches')) {
    dispatched.push(JSON.parse(opts.body));
    return new Response(null,{status:204});
  }
  throw Error('Unexpected '+url);
};
const env = {TELEGRAM_TOKEN:'x',TELEGRAM_CHAT_ID:'999',GITHUB_PAT:'x',
  GITHUB_REPO:'owner/repo',TELEGRAM_WEBHOOK_SECRET:'secret',
  BOT_STATE:{get:async k=>kv.get(k)||null,put:async(k,v)=>{kv.set(k,v);}}};
let index=1;
async function hook(payload) {
  const res=await worker.fetch(new Request('https://worker.example',{method:'POST',
   headers:{'content-type':'application/json','X-Telegram-Bot-Api-Secret-Token':'secret'},
   body:JSON.stringify({update_id:index++,...payload})}),env);
  assert.equal(res.status,200);
}
async function msg(text) {await hook({message:{chat:{id:999},text}});}
async function press(data,owner=999) {
  await hook({callback_query:{id:'cb-'+index,from:{id:owner},data,message:{chat:{id:999}}}});
}
function lastMarkup() {
  const latest=sent.filter(e=>e.method==='sendMessage').at(-1)?.data;
  assert.ok(latest?.reply_markup?.inline_keyboard,'expected button markup');
  return latest.reply_markup.inline_keyboard.flat();
}
function button(label) {
  const choice=lastMarkup().find(item=>item.text.includes(label));
  assert.ok(choice, 'Missing button '+label);
  return choice.callback_data;
}
await msg('/menu');
assert.ok(button('تحليل'));
assert.ok(button('السيو'));
assert.ok(button('الحالة'));

// Source command dispatches prepare only; never publishing.
const prep=button('تحليل');
await press(prep);
assert.equal(dispatched.at(-1).client_payload.agent_action,'prepare');
assert.equal(dispatched.length,1);
plan=structuredClone(initial);
await msg('/menu');
let toggle3=button('3.');
await press(toggle3);
assert.match(sent.filter(x=>x.method==='sendMessage').at(-1).data.text,/3/);
assert.ok(button('✅ 3.'));
let preview=button('معاينة');
await press(preview);
assert.equal(dispatched.at(-1).client_payload.clip_ids,'3');
assert.equal(dispatched.at(-1).client_payload.agent_action,'preview');

// Old plan buttons cannot operate on a later plan.
const stale=toggle3;
plan.created_utc='2026-11-16T00:50:00.000001+00:00';
const previousCount=dispatched.length;
await press(stale);
assert.equal(dispatched.length,previousCount);

// Identity check: other Telegram user cannot approve.
plan=structuredClone(initial);
plan.status='previewed_awaiting_approval';
plan.previewed_ids=[3];
plan.preview_run_id='13579';
await msg('/menu');
const approve=button('اعتماد المعاينة');
await press(approve,111);
assert.equal(plan.status,'previewed_awaiting_approval');
await press(approve);
assert.equal(plan.status,'approved');
assert.deepEqual(plan.approved_ids,[3]);
assert.ok(plan.approval_utc);
await msg('/menu');
const confirm=button('تأكيد النشر الخاص');
await press(confirm);
assert.equal(dispatched.length,previousCount,'first click is only a confirmation');
await press(button('نعم، انشر خاص'));
assert.equal(plan.status,'publish_queued');
assert.equal(dispatched.at(-1).client_payload.clip_ids,'3');
const publishedCount=dispatched.length;
await press(confirm);
assert.equal(dispatched.length,publishedCount,'old button cannot republish');

// Auth failed after token refresh before upload, explicit second-confirmation retry.
plan={...structuredClone(initial),status:'auth_failed',previewed_ids:[3],preview_run_id:'13579',
  approved_ids:[3],approval_utc:'2026-11-16T00:55:00Z',
  auth_error_code:'YOUTUBE_OAUTH_INVALID_GRANT',published_results:[]};
await msg('/menu');
await press(button('إعادة المحاولة'));
assert.equal(dispatched.length,publishedCount);
await press(button('نعم، أعد المحاولة'));
assert.equal(plan.status,'publish_queued');
assert.equal(dispatched.length,publishedCount+1);
assert.equal(dispatched.at(-1).client_payload.clip_ids,'3');

// No arbitrary reset of ambiguous 'publishing' state.
plan.status='publishing';
plan.auth_error_code='';
await msg('/menu');
assert.ok(!lastMarkup().some(x=>x.text.includes('إعادة المحاولة')));
console.log('Telegram button/preview/approval/confirm/retry offline tests passed');
