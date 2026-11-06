import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker = (await import(pathToFileURL(new URL('../worker.js',import.meta.url).pathname))).default;
const source = {video_url:'https://youtu.be/wQUPA9K2MSY',telegram_file_id:'abc'};
const base={version:1, status:'previewed_awaiting_approval', created_utc:'2026-11-04T07:13:07.382901+00:00',
  ...source, duration:92, previewed_ids:[1], preview_run_id:'12345',
  clips:[{id:1, title:'First', start_time:0, end_time:30},{id:2,title:'Second',start_time:30,end_time:55}]};
let plan = structuredClone(base), artifactsReady=true, dispatched=[],sent=[];
const kv=new Map([['source:999',JSON.stringify(source)]]);
globalThis.fetch=async(url,opts={})=>{
 if(url.startsWith('https://api.telegram.org/')) {sent.push(JSON.parse(opts.body)); return Response.json({ok:true,result:true});}
 if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')) {
   if(opts.method==='PATCH') {plan=JSON.parse(JSON.parse(opts.body).value);return new Response(null,{status:204});}
   return Response.json({value:JSON.stringify(plan)});
 }
 if(url.endsWith('/actions/runs/12345/artifacts'))
   return Response.json({artifacts:artifactsReady?[{name:'preview-12345',expired:false}]:[]});
 if(url.endsWith('/dispatches')) {dispatched.push(JSON.parse(opts.body));return new Response(null,{status:204});}
 throw Error('Unexpected fetch '+url);
};
const env={TELEGRAM_TOKEN:'mock',TELEGRAM_CHAT_ID:'999', GITHUB_PAT:'mock',GITHUB_REPO:'owner/repo',
 TELEGRAM_WEBHOOK_SECRET:'secret', BOT_STATE:{get:async(k)=>kv.get(k)||null,put:async(k,v)=>{kv.set(k,v);}}};
let n=990;
async function sendMessage(text){const req=new Request('https://worker.example',{method:'POST',headers:{'content-type':'application/json', 'X-Telegram-Bot-Api-Secret-Token':'secret'},body:JSON.stringify({update_id:n++,message:{chat:{id:999},text}})});const result=await worker.fetch(req,env);assert.equal(result.status,200);return sent.at(-1).text;}
await sendMessage('/publish'); assert.equal(dispatched.length,0); assert.equal(plan.status,'previewed_awaiting_approval');
await sendMessage('/approve 2'); assert.equal(plan.status,'previewed_awaiting_approval');
artifactsReady=false;
await sendMessage('/approve 1');assert.equal(plan.status,'previewed_awaiting_approval');
artifactsReady=true;
await sendMessage('/approve ١');assert.deepEqual(plan.approved_ids,[1]);assert.equal(plan.status,'approved');
await sendMessage('/publish'); assert.equal(plan.status,'publish_queued');assert.equal(dispatched.length,1);
assert.equal(dispatched[0].client_payload.agent_action,'publish');
assert.equal(dispatched[0].client_payload.clip_ids,'1');
assert.equal(dispatched[0].client_payload.clip_plan_ref,base.created_utc);
await sendMessage('/publish');assert.equal(dispatched.length,1);
await sendMessage('/cancel');assert.equal(plan.status,'publish_queued');
await sendMessage('/prepare');assert.equal(plan.status,'publish_queued');
console.log('Approval/publish Worker tests passed: 8 scenarios');
