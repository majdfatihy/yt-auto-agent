import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const worker = (await import(pathToFileURL(new URL('../worker.js', import.meta.url).pathname))).default;

const source = {video_url:'https://drive.google.com/file/d/1sSnNazEBqmooLiRkt5D7ZLnp3gRuNn-O/view',telegram_file_id:''};
const kv = new Map([['source:999',JSON.stringify(source)]]);
const calls = [];
const hooked = {
  url:'https://majd-youtube-agent.example.workers.dev/',
  allowed_updates:['message','edited_message'], max_connections:17
};
globalThis.fetch=async(url,opts={})=>{
  if(url.startsWith('https://api.telegram.org/bot')) {
    const method=url.split('/').at(-1), data=JSON.parse(opts.body);
    calls.push({method,data});
    if(method==='getWebhookInfo') return Response.json({ok:true,result:{...hooked}});
    if(method==='setWebhook') {
      assert.equal(data.url, hooked.url);
      assert.equal(data.secret_token,'secret');
      assert.equal(data.drop_pending_updates,false);
      assert.equal(data.max_connections,17);
      assert.deepEqual(data.allowed_updates,['message','edited_message','callback_query']);
      hooked.allowed_updates=data.allowed_updates;
      return Response.json({ok:true,result:true});
    }
    return Response.json({ok:true,result:true});
  }
  if(url.endsWith('/actions/variables/TELEGRAM_CLIP_PROPOSALS'))
    return new Response(null,{status:404});
  throw Error('Unexpected fetch '+url);
};
const env={
  TELEGRAM_TOKEN:'fake-token',TELEGRAM_CHAT_ID:'999',GITHUB_PAT:'fake',
  GITHUB_REPO:'owner/repo',TELEGRAM_WEBHOOK_SECRET:'secret',
  BOT_STATE:{get:async k=>kv.get(k)||null,put:async(k,v)=>{kv.set(k,v);}}
};
let counter=100;
async function sendUpdate(body) {
  const result=await worker.fetch(new Request('https://majd-youtube-agent.example.workers.dev/',{
    method:'POST',
    headers:{'X-Telegram-Bot-Api-Secret-Token':'secret','content-type':'application/json'},
    body:JSON.stringify({update_id:counter++,...body})
  }),env);
  assert.equal(result.status,200);
}
await sendUpdate({message:{chat:{id:999},from:{id:999},text:'/menu'}});
assert.equal(calls.filter(x=>x.method==='setWebhook').length,1);
assert.equal(calls.filter(x=>x.method==='getWebhookInfo').length,2);
const menu=calls.filter(x=>x.method==='sendMessage').at(-1).data;
assert.match(menu.text,/تم تفعيل استقبال الأزرار/);
assert.ok(menu.reply_markup?.inline_keyboard?.length);
const status=menu.reply_markup.inline_keyboard.flat().find(b=>b.text.includes('الحالة'));
assert.ok(status?.callback_data);
await sendUpdate({callback_query:{
  id:'callback-100',from:{id:999},
  message:{chat:{id:999},message_id:775},data:status.callback_data
}});
assert.equal(calls.filter(x=>x.method==='answerCallbackQuery').length,1);
assert.equal(calls.filter(x=>x.method==='editMessageText').at(-1).data.message_id,775);
await sendUpdate({message:{chat:{id:999},from:{id:999},text:'/menu'}});
assert.equal(calls.filter(x=>x.method==='setWebhook').length,1,'already-enabled webhook must not be reset');

// When another owner clicks, the callback must be acknowledged with an error,
// not silently hang, and should not change state.
env.TELEGRAM_OWNER_ID='999';
await sendUpdate({callback_query:{
  id:'invalid-user',from:{id:333},
  message:{chat:{id:999},message_id:775},data:status.callback_data
}});
assert.equal(calls.filter(x=>x.method==='answerCallbackQuery').at(-1).data.show_alert,true);
assert.equal(calls.filter(x=>x.method==='setWebhook').length,1);

// Empty/default filters include callback_query, so no unnecessary webhook changes.
delete hooked.allowed_updates;
await sendUpdate({message:{chat:{id:999},text:'/menu'}});
assert.equal(calls.filter(x=>x.method==='setWebhook').length,1);

console.log('Webhook callback subscription, safe re-registration, owner rejection and button handling passed');
