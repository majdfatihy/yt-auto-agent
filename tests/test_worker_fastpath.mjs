// One incoming link should trigger one automatic preview job, no upload.
// A repeated link within ten minutes must not duplicate the expensive job.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker=(await import(pathToFileURL(new URL('../worker.js',import.meta.url).pathname))).default;
const source={video_url:'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',telegram_file_id:''};
const kv=new Map(), sent=[], dispatches=[];
let plan=null, updateId=900;
globalThis.fetch=async (url,opts={})=>{
  if(url.startsWith('https://api.telegram.org/')) {
    sent.push({method:url.split('/').at(-1),payload:JSON.parse(opts.body)});
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS')) {
    if(opts.method==='PATCH') {plan=JSON.parse(JSON.parse(opts.body).value);return new Response(null,{status:204});}
    return plan?Response.json({value:JSON.stringify(plan)}):new Response(null,{status:404});
  }
  if(url.endsWith('/dispatches')) {
    dispatches.push(JSON.parse(opts.body));
    return new Response(null,{status:204});
  }
  throw Error('Unexpected URL '+url);
};
const env={TELEGRAM_TOKEN:'fake',TELEGRAM_CHAT_ID:'999',GITHUB_PAT:'fake',
  GITHUB_REPO:'owner/repo',TELEGRAM_WEBHOOK_SECRET:'secret',
  BOT_STATE:{get:async key=>kv.get(key)||null,put:async(key,value)=>{kv.set(key,value);}}};
async function msg(text) {
  const response=await worker.fetch(new Request('https://test.example',{
    method:'POST',headers:{'content-type':'application/json','X-Telegram-Bot-Api-Secret-Token':'secret'},
    body:JSON.stringify({update_id:updateId++,message:{chat:{id:999},text}})
  }),env);
  assert.equal(response.status,200);
}
await msg(source.video_url);
assert.equal(dispatches.length,1);
assert.equal(dispatches[0].client_payload.agent_action,'prepare');
assert.equal(dispatches[0].client_payload.video_url,source.video_url);
assert.equal(sent.at(-1).method,'sendMessage');
assert.match(sent.at(-1).payload.text,/مفيش نشر قبل موافقتك/);
await msg(source.video_url);
assert.equal(dispatches.length,1,'repeated link must not start another job');
assert.match(sent.at(-1).payload.text,/تلقائيًا/);
await msg('/prepare');
assert.equal(dispatches.length,2,'manual reprocess must remain possible on demand');
assert.equal(dispatches[1].client_payload.agent_action,'prepare');
assert.ok(!dispatches.some(x=>x.client_payload.agent_action==='publish'));
console.log('Automatic link -> single preview run, duplicate protection and no auto-publish passed');
