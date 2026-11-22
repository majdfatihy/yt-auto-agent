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
 await socialCommand(env,'/postpublish تأكيد');
 state=JSON.parse(data.get('social:draft:123'));
 assert.equal(state.results.facebook.status,'published');
 assert.equal(state.results.instagram.status,'needs_review');
 assert.equal(await socialTick(env),'needs_review');
 console.log('social publishing offline tests passed');
}finally{globalThis.fetch=old;}
