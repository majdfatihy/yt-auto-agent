import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker=(await import(pathToFileURL(new URL('../worker.js',import.meta.url).pathname))).default;
const source={video_url:'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',telegram_file_id:''};
const base={version:2,status:'awaiting_selection',created_utc:'2026-11-16T01:00:00Z',
  ...source,duration:210,
  clips:[{id:1,title:'ريلز 2',start_time:0,end_time:32},
         {id:2,title:'ريلز 3',start_time:33,end_time:66},
         {id:3,title:'ريلز 4',start_time:106.5,end_time:150},
         {id:4,title:'ريلز 5',start_time:150,end_time:195}]};
let plan=structuredClone(base), sent=[], dispatches=[], num=0;
const kv=new Map([['source:999',JSON.stringify(source)]]);
globalThis.fetch=async (url,opts={})=>{
  if(url.startsWith('https://api.telegram.org/')) {
    const method=url.split('/').at(-1);
    sent.push({method,data:JSON.parse(opts.body)});
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')) {
    if(opts.method==='PATCH') {
      plan=JSON.parse(JSON.parse(opts.body).value);
      return new Response(null,{status:204});
    }
    return Response.json({value:JSON.stringify(plan)});
  }
  if(url.endsWith('/actions/runs/13579/artifacts')) {
    return Response.json({artifacts:[{name:'preview-13579',expired:false}]});
  }
  if(url.endsWith('/dispatches')) {
    dispatches.push(JSON.parse(opts.body));
    return new Response(null,{status:204});
  }
  throw Error('Unexpected URL '+url);
};
const env={TELEGRAM_TOKEN:'mock',TELEGRAM_CHAT_ID:'999',TELEGRAM_OWNER_ID:'999',
  GITHUB_PAT:'mock',GITHUB_REPO:'owner/repo',TELEGRAM_WEBHOOK_SECRET:'secret',
  BOT_STATE:{get:async k=>kv.get(k)||null,put:async(k,v)=>{kv.set(k,v);}}};
async function request(body) {
  const res=await worker.fetch(new Request('https://worker.local',{
    method:'POST',
    headers:{'content-type':'application/json','X-Telegram-Bot-Api-Secret-Token':'secret'},
    body:JSON.stringify({update_id:++num,...body})
  }),env);
  assert.equal(res.status,200);
}
async function msg(text){await request({message:{chat:{id:999},from:{id:999},text}});}
async function click(data){await request({callback_query:{
  id:'cb-'+num,from:{id:999},data,message:{chat:{id:999},message_id:111}}});}
function lastMarkup() {
  return sent.filter(x=>['sendMessage','editMessageText'].includes(x.method))
    .at(-1).data.reply_markup.inline_keyboard.flat();
}
function callback(label){
  const hit=lastMarkup().find(x=>x.text.includes(label));
  assert.ok(hit,'missing '+label);return hit.callback_data;
}
await msg('/menu');
await click(callback('معاينة الكل'));
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.agent_action,'preview');
assert.equal(dispatches[0].client_payload.clip_ids,'1,2,3,4');
assert.equal(plan.status,'awaiting_selection','preview must not publish');
plan.status='previewed_awaiting_approval';
plan.previewed_ids=[1,2,3,4];
plan.preview_run_id='13579';
await msg('/menu');
await click(callback('تحديد كل المعاينات'));
assert.ok(lastMarkup().some(x=>x.text.includes('✅ 4.')));
await click(callback('إلغاء التحديد'));
assert.ok(lastMarkup().some(x=>x.text.includes('▫️ 4.')));
await click(callback('1.'));
await click(callback('3.'));
const approve=callback('اعتماد ونشر المحدد');
await click(approve);
assert.deepEqual(plan.approved_ids,[1,3]);
assert.equal(plan.status,'publish_queued');
assert.equal(dispatches.length,2);
assert.equal(dispatches.at(-1).client_payload.clip_ids,'1,3');
await click(approve);
assert.equal(dispatches.length,2,'repeated approval cannot dispatch again');
console.log('Preview all, custom multi-select publication and replay protection passed');
