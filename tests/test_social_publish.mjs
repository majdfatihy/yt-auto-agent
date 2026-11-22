import assert from 'node:assert/strict';
import {socialCommand,socialTick} from '../social_publish.js';
const data=new Map(), sent=[];
const env={TELEGRAM_CHAT_ID:'123',TELEGRAM_TOKEN:'test',BOT_STATE:{
 get:async k=>data.get(k)||null,put:async(k,v)=>{data.set(k,v)},delete:async k=>{data.delete(k)}
},FB_PAGE_ID:'page',FB_ACCESS_TOKEN:'token'};
const old=globalThis.fetch;
globalThis.fetch=async(url,opts)=> {
 if(String(url).includes('api.telegram.org')){sent.push(JSON.parse(opts.body).text);return {ok:true,json:async()=>({ok:true,result:{}})};}
 if(String(url).includes('graph.facebook.com'))return {ok:true,json:async()=>({id:'post123'})};
 throw Error('unexpected network');
};
try{
 await socialCommand(env,'/post Test publication');
 await socialCommand(env,'/targets facebook');
 await socialCommand(env,'/postreview');
 assert.equal(sent.at(-1).includes('Test publication'),true);
 await socialCommand(env,'/postpublish تأكيد');
 let state=JSON.parse(data.get('social:draft:123'));
 assert.equal(state.state,'published');
 assert.equal(state.results.facebook.id,'post123');
 await assert.rejects(()=>socialCommand(env,'/postpublish تأكيد'),/لا تعيد الإرسال/);
 await socialCommand(env,'/postimage https://example.com/a.jpg | New image');
 await socialCommand(env,'/targets facebook,instagram');
 await assert.rejects(()=>socialCommand(env,'/postpublish تأكيد'),/راجع المنشور/);
 await assert.rejects(()=>socialCommand(env,'/postreview'),/Instagram يتطلب صورة أو فيديو/);
 await assert.rejects(()=>socialCommand(env,'/postpublish تأكيد'),/راجع المنشور/);
 await socialCommand(env,'/targets facebook');
 await socialCommand(env,'/postreview');
 await socialCommand(env,'/postpublish تأكيد');
 state=JSON.parse(data.get('social:draft:123'));
 assert.equal(state.results.facebook.status,'published');
 assert.equal(state.results.instagram,undefined);
 assert.equal(await socialTick(env),'processed:0');
 await socialCommand(env,'/postlist');
 assert.equal(sent.at(-1).includes('published'),true);
 // Cancelled scheduled jobs must never publish, even after their due time.
 await socialCommand(env,'/post Cancelled scheduled job');
 await socialCommand(env,'/targets facebook');
 await socialCommand(env,'/postreview');
 await socialCommand(env,'/postschedule 2099-01-01T12:00Z');
 assert.equal(JSON.parse(data.get('social:draft:123')).state,'pending_schedule');
 await socialCommand(env,'/postscheduleconfirm تأكيد');
 const cancelledId=JSON.parse(data.get('social:draft:123')).id;
 await socialCommand(env,'/postcancel');
 let cancelled=JSON.parse(data.get('social:item:123:'+cancelledId));
 assert.equal(cancelled.state,'cancelled');
 cancelled.due='2000-01-01T00:00:00Z';
 data.set('social:item:123:'+cancelledId,JSON.stringify(cancelled));
 assert.equal(await socialTick(env),'processed:0');
 assert.equal(JSON.parse(data.get('social:item:123:'+cancelledId)).state,'cancelled');
 // A due scheduled post must not overwrite a newer, unrelated draft.
 await socialCommand(env,'/post First scheduled job');
 await socialCommand(env,'/targets facebook');
 await socialCommand(env,'/postreview');
 await socialCommand(env,'/postschedule 2099-01-01T12:00Z');
 await socialCommand(env,'/postscheduleconfirm تأكيد');
 const scheduledId=JSON.parse(data.get('social:draft:123')).id;
 let scheduled=JSON.parse(data.get('social:item:123:'+scheduledId));
 scheduled.due='2000-01-01T00:00:00Z';
 data.set('social:item:123:'+scheduledId,JSON.stringify(scheduled));
 await socialCommand(env,'/post New untouched draft');
 const newDraft=JSON.parse(data.get('social:draft:123'));
 assert.equal(await socialTick(env),'processed:1');
 assert.equal(JSON.parse(data.get('social:item:123:'+scheduledId)).state,'published');
 assert.equal(JSON.parse(data.get('social:draft:123')).id,newDraft.id);
 assert.equal(JSON.parse(data.get('social:draft:123')).state,'draft');
 console.log('social publishing offline tests passed');
}finally{globalThis.fetch=old;}
