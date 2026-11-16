import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import worker from '../worker.js';

const source={video_url:'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',telegram_file_id:''};
const clips=[1,2,3].map((id)=>({
  id,serial:id,title:'شرح استقبال الحوالات الدولية عبر فودافون كاش #فودافون_كاش '+id,
  reel_title:'شرح استقبال الحوالات الدولية عبر فودافون كاش #فودافون_كاش '+id,
  start_time:(id-1)*40,end_time:id*40,seo_ready:true,reel_seo_version:3,
  reel_description:'جملة أولى عن استقبال الحوالات الدولية على فودافون كاش.\nجملة ثانية عن استقبال الأموال.\nجملة ثالثة عن خطوات التحويل.\n#فودافون_كاش #حوالات',
  reel_tags_string:'فودافون كاش, حوالات دولية, تحويلات خارجية',reel_language:'ar'
}));
const contents={plan_ref:'2026-11-16T00:00:00Z',
  texts:{'1':'هذا هو النص المنطوق الكامل الخاص بالريلز الأول من الفيديو.',
         '2':'نص الريلز الثاني الكامل.','3':'نص الريلز الثالث الكامل.'},
  cues:[]};
const raw=Buffer.from(JSON.stringify(contents),'utf8');
const sha=createHash('sha256').update(raw).digest('hex');
const b64=gzipSync(raw).toString('base64');
let plan={method:'topic_transcript_ai',version:3,status:'awaiting_selection',
  ...source,clips,clip_jobs:{},created_utc:contents.plan_ref,
  transcript_chunks:1,transcript_sha256:sha};
const kv=new Map([['source:999',JSON.stringify(source)]]);
const sent=[],dispatches=[];
let failNextDispatch=false;
globalThis.fetch=async(url,opts={})=>{
  if(url.startsWith('https://api.telegram.org/')){
    const name=url.split('/').at(-1),json=JSON.parse(opts.body||'{}');
    sent.push({name,json});
    if(name==='getWebhookInfo')return Response.json({ok:true,result:{
      url:'https://example.workers.dev',allowed_updates:['message','callback_query']}});
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')){
    if(opts.method==='PATCH'){plan=JSON.parse(JSON.parse(opts.body).value);
      return new Response(null,{status:204});}
    return Response.json({value:JSON.stringify(plan)});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_REEL_TEXT_0'))
    return Response.json({value:b64});
  if(url.endsWith('/dispatches')){
    if(failNextDispatch){failNextDispatch=false;return new Response('unavailable',{status:504});}
    dispatches.push(JSON.parse(opts.body));return new Response(null,{status:204});
  }
  throw Error('Unexpected URL '+url);
};
const env={TELEGRAM_CHAT_ID:'999',TELEGRAM_OWNER_ID:'999',
  TELEGRAM_TOKEN:'fake',TELEGRAM_WEBHOOK_SECRET:'secret',
  GITHUB_PAT:'fake',GITHUB_REPO:'owner/repo',
  BOT_STATE:{get:async k=>kv.get(k)||null,put:async(k,v)=>{kv.set(k,v);}}};
let id=1000;
async function request(obj) {
  const response=await worker.fetch(new Request('https://bot.local',{method:'POST',
    headers:{'content-type':'application/json','X-Telegram-Bot-Api-Secret-Token':'secret'},
    body:JSON.stringify({update_id:id++,...obj})}),env);
  assert.equal(response.status,200);
}
async function msg(text){return request({message:{chat:{id:999},from:{id:999},text}});}
async function click(cb){return request({callback_query:{id:'cb'+id,from:{id:999},
  message:{chat:{id:999},message_id:88},data:cb}});}
function messages(){return sent.filter(x=>x.name==='sendMessage'||x.name==='editMessageText');}
function button(part){
  for(const s of messages().slice().reverse()){
    const b=s.json.reply_markup?.inline_keyboard?.flat()?.find(x=>x.text.includes(part));
    if(b)return b.callback_data;
  }
  throw Error('Missing callback '+part);
}
await msg('/menu');
assert.ok(messages().at(-1).json.text.includes(clips[0].reel_title));
assert.ok(messages().at(-1).json.text.includes('00:40'));
await click(button('عرض النص والتفاصيل 1'));
assert.ok(messages().at(-1).json.text.includes(contents.texts['1']));
await msg('/privacy 1 unlisted');
assert.equal(plan.clip_settings['1'].privacy,'unlisted');
await msg('/captions 1 off');
assert.equal(plan.clip_settings['1'].captions,false);
// Item 7: global defaults and individual overrides must reach the actual preview job.
await msg('/blur off');
await msg('/blur 1 on');
await msg('/clip 1');
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.agent_action,'preview');
const previewOptions=JSON.parse(dispatches[0].client_payload.clip_options);
assert.deepEqual(previewOptions['1'],{captions:false,blur:'on',privacy:'unlisted'});
dispatches.length=0; // Preview dispatch is deliberately not a YouTube upload.
await msg('/title 1 طريقة جديدة لاستقبال الحوالة عبر فودافون كاش');
assert.ok(plan.clips[0].reel_title.endsWith('#فودافون_كاش 1'));
await msg('/menu');
await click(button('اعتماد عناوين المحدد'));
assert.ok(plan.clips.every(x=>x.title_approved));
const future=new Date(Date.now()+14*86400000);
const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Africa/Cairo',
  year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(future);
const get=k=>parts.find(p=>p.type===k).value;
const start=get('year')+'-'+get('month')+'-'+get('day')+' '+get('hour')+':'+get('minute');
await msg('/schedule '+start+' 1h');
assert.equal(Date.parse(plan.schedule_draft.items['2'])-
  Date.parse(plan.schedule_draft.items['1']),3600000);
await msg('/schedule '+start+' 1d');
assert.equal(Date.parse(plan.schedule_draft.items['3'])-
  Date.parse(plan.schedule_draft.items['2']),86400000);
await msg('/schedule '+start+' 10m');
assert.deepEqual(plan.schedule_draft.ids,[1,2,3]);
assert.equal(Date.parse(plan.schedule_draft.items['2'])-
  Date.parse(plan.schedule_draft.items['1']),600000);
await click(button('اعتماد وتنفيذ الجدولة'));
assert.equal(plan.clip_jobs['1'].state,'pending');
assert.equal(plan.clip_jobs['2'].state,'pending');
assert.equal(plan.clip_jobs['1'].privacy,'unlisted');
assert.equal(plan.clip_jobs['1'].captions,false);
assert.equal(dispatches.length,0,'no publishing before due');
await msg('/pause');
assert.equal(plan.schedule_paused,true);
await worker.scheduled({scheduledTime:Date.parse(plan.clip_jobs['1'].due_utc)+60000},env,{});
assert.equal(dispatches.length,0,'pause must prevent future scheduled dispatches');
await msg('/resume');
assert.equal(plan.schedule_paused,false);
await worker.scheduled({scheduledTime:Date.parse(plan.clip_jobs['1'].due_utc)+60000},env,{});
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.agent_action,'direct_publish');
assert.equal(dispatches[0].client_payload.clip_ids,'1');
assert.equal(plan.clip_jobs['1'].state,'queued');
await worker.scheduled({scheduledTime:Date.parse(plan.clip_jobs['2'].due_utc)+60000},env,{});
assert.equal(dispatches.length,1,'no overlapping job dispatches');
await msg('/reschedule 2 '+start);
assert.equal(plan.clip_jobs['2'].state,'pending');
await msg('/stop');
assert.equal(plan.clip_jobs['2'].state,'cancelled');
assert.equal(plan.clip_jobs['3'].state,'cancelled');
assert.equal(plan.clip_jobs['1'].state,'queued');
// Item 8: scheduling a single reel separately retains public privacy and needs approval.
plan={...plan,created_utc:'2026-11-16T02:00:00Z',
  clip_jobs:{},schedule_paused:false};
await msg('/schedule 2 '+start);
assert.deepEqual(plan.schedule_draft.ids,[2]);
assert.equal(dispatches.length,1,'draft must not upload before approval');
await click(button('اعتماد وتنفيذ الجدولة'));
assert.equal(plan.clip_jobs['2'].state,'pending');
assert.equal(plan.clip_jobs['2'].privacy,'public');
assert.equal(dispatches.length,1,'pending public reel must not upload early');
// HTTP failure may happen after GitHub accepted a dispatch. Lock and do not retry.
plan={...plan,created_utc:'2026-11-17T00:00:00Z',clip_jobs:{},schedule_paused:false};
failNextDispatch=true;
await msg('/publishnow 1');
assert.equal(plan.clip_jobs['1'].state,'dispatch_unknown');
assert.equal(dispatches.length,1);
await msg('/publishnow 1');
assert.equal(dispatches.length,1,'same reel must never be re-dispatched after ambiguous response');
console.log('Topic management, schedules and ambiguous publishing safety passed.');
