import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker=(await import(pathToFileURL(new URL('../worker.js',import.meta.url).pathname))).default;
const source={video_url:'https://youtu.be/wQUPA9K2MSY',telegram_file_id:''};
const original={version:2,status:'published',created_utc:'2026-11-16T02:00:00.000001+00:00',
  ...source,previewed_ids:[1],preview_run_id:'12345',approved_ids:[1],
  approval_utc:'2026-11-16T02:01:00Z',duration:120,
  published_results:[{clip_id:1,result:'✅ يوتيوب: تم رفع الفيديو (https://youtu.be/abcdefghijk)'}],
  clips:[{id:1,title:'مقطع تم رفعه',start_time:0,end_time:35},
         {id:2,title:'مقطع غير منشور',start_time:36,end_time:70},
         {id:3,title:'مقطع جديد',start_time:71,end_time:110}]};
let plan=structuredClone(original),events=[],sent=[],n=1;
const state=new Map([['source:999',JSON.stringify(source)]]);
globalThis.fetch=async(url,opt={})=>{
  if(url.startsWith('https://api.telegram.org/')){
    sent.push({type:url.split('/').at(-1),data:JSON.parse(opt.body)});
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')){
    if(opt.method==='PATCH'){
      plan=JSON.parse(JSON.parse(opt.body).value);
      return new Response(null,{status:204});
    }
    return Response.json({value:JSON.stringify(plan)});
  }
  if(url.endsWith('/dispatches')){
    events.push(JSON.parse(opt.body));
    return new Response(null,{status:204});
  }
  throw Error('unexpected '+url);
};
const env={TELEGRAM_TOKEN:'test',TELEGRAM_CHAT_ID:'999',
  GITHUB_REPO:'owner/repo',GITHUB_PAT:'test',TELEGRAM_WEBHOOK_SECRET:'secret',
  BOT_STATE:{get:async k=>state.get(k)||null,put:async(k,v)=>state.set(k,v)}};
async function act(payload){
  const response=await worker.fetch(new Request('https://example.com',{
    method:'POST',headers:{'content-type':'application/json',
      'X-Telegram-Bot-Api-Secret-Token':'secret'},
    body:JSON.stringify({update_id:n++,...payload})
  }),env);
  assert.equal(response.status,200);
}
async function msg(text){await act({message:{chat:{id:999},from:{id:999},text}})}
async function press(data){await act({callback_query:{id:'cb-'+n,from:{id:999},
  data,message:{chat:{id:999},message_id:102}}})}
function button(substring){
  const m=sent.filter(x=>['sendMessage','editMessageText'].includes(x.type)).at(-1);
  const entries=m?.data?.reply_markup?.inline_keyboard?.flat()||[];
  const found=entries.find(x=>x.text.includes(substring));
  assert.ok(found,'missing button '+substring);
  return found.callback_data;
}
await msg('/menu');
const resume=button('أكمل الريلز المتبقية');
await press(resume);
assert.equal(plan.status,'awaiting_selection');
assert.deepEqual(plan.clips.map(x=>x.id),[2,3]);
assert.deepEqual(plan.previewed_ids,[]);
assert.deepEqual(plan.approved_ids,[]);
assert.equal(plan.preview_run_id,null);
assert.equal(plan.created_utc,original.created_utc);
assert.equal(events.length,0,'resuming the source should not dispatch /prepare');
await msg('/menu');
const t2=button('2.');
await press(t2);
assert.ok(sent.at(-1).data.text.includes('2'));
assert.equal(events.length,0);
await press(resume);
assert.equal(plan.status,'awaiting_selection','stale resume must not modify current plan');
// A legacy plan lacking confirmed upload proof must not be unlocked.
plan=structuredClone(original);
plan.published_results=[];
await msg('/continue');
assert.equal(plan.status,'published');
assert.match(sent.at(-1).data.text,/لا توجد ريلز متبقية موثقة/);
console.log('Resume existing source after published, preserve remaining, reject stale and unverified publish state');
