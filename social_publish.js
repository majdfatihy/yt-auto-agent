// Owner-approved Meta publishing. Persistent state uses existing BOT_STATE KV.
// Credentials are Cloudflare secrets; never stored in KV or Telegram messages.
const PLATFORMS=['facebook','instagram','threads'];
const key=e=>'social:draft:'+e.TELEGRAM_CHAT_ID;
const itemKey=(e,id)=>'social:item:'+e.TELEGRAM_CHAT_ID+':'+id;
const indexKey=e=>'social:index:'+e.TELEGRAM_CHAT_ID;
async function indexItem(e,id){const ids=JSON.parse(await e.BOT_STATE.get(indexKey(e))||'[]');if(!ids.includes(id)){ids.push(id);await e.BOT_STATE.put(indexKey(e),JSON.stringify(ids.slice(-250)));}}
async function storeItem(e,d){await save(e,itemKey(e,d.id),d);await indexItem(e,d.id);}
async function listItems(e){const ids=JSON.parse(await e.BOT_STATE.get(indexKey(e))||'[]');return (await Promise.all(ids.map(id=>read(e,itemKey(e,id))))).filter(Boolean);}
const API='https://graph.facebook.com/v22.0';
const THREADS='https://graph.threads.net/v1.0';
const message=async(e,t)=>{const r=await fetch('https://api.telegram.org/bot'+e.TELEGRAM_TOKEN+'/sendMessage',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({chat_id:e.TELEGRAM_CHAT_ID,text:t})});if(!r.ok)throw Error('Telegram notification failed');};
const read=async(e,k)=>JSON.parse(await e.BOT_STATE.get(k)||'null');
const save=async(e,k,v)=>e.BOT_STATE.put(k,JSON.stringify(v));
function supported(e,p){
 if(p==='facebook')return !!((e.FB_PAGE_ID&&e.FB_ACCESS_TOKEN)||(e.GITHUB_PAT&&e.GITHUB_REPO));
 if(p==='instagram')return !!(e.IG_USER_ID&&e.IG_ACCESS_TOKEN);
 if(p==='threads')return !!(e.THREADS_USER_ID&&e.THREADS_ACCESS_TOKEN);
 return false;
}
async function call(base,path,token,body,method='POST'){
 const u=new URL(base+'/'+path.replace(/^\//,''));
 const r=await fetch(u,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({}));
 if(!r.ok||j.error)throw Error((j.error?.message||'API HTTP '+r.status).slice(0,220));
 return j;
}
async function dispatchFacebook(e,d){
 if(!e.GITHUB_PAT||!e.GITHUB_REPO)throw Error('GitHub dispatch credentials missing');
 const response=await fetch('https://api.github.com/repos/'+e.GITHUB_REPO+'/dispatches',{
  method:'POST',headers:{Authorization:'Bearer '+e.GITHUB_PAT,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'yt-auto-agent','Content-Type':'application/json'},
  body:JSON.stringify({event_type:'publish_approved_facebook_post',client_payload:{id:d.id,type:d.type,text:d.text,url:d.url||''}})
 });
 if(response.status!==204)throw Error('GitHub Facebook dispatch HTTP '+response.status);
 return {queued_external:true};
}
async function publishOne(e,p,d){
 if(p==='facebook'){
  if(!(e.FB_PAGE_ID&&e.FB_ACCESS_TOKEN))return dispatchFacebook(e,d);
  const id=e.FB_PAGE_ID,t=e.FB_ACCESS_TOKEN;
  if(d.type==='text'||d.type==='link')return call(API,id+'/feed',t,{message:d.text,...(d.type==='link'?{link:d.url}:{})});
  if(d.type==='image')return call(API,id+'/photos',t,{url:d.url,caption:d.text});
  if(d.type==='video')return call(API,id+'/videos',t,{file_url:d.url,description:d.text});
 }
 if(p==='instagram'){
  if(!['image','video'].includes(d.type))throw Error('Instagram requires image or video; text/link-only unsupported');
  const id=e.IG_USER_ID,t=e.IG_ACCESS_TOKEN;
  const container=await call(API,id+'/media',t,d.type==='image'?{image_url:d.url,caption:d.text}:{media_type:'REELS',video_url:d.url,caption:d.text});
  // Video processing is asynchronous. Do not publish until FINISHED.
  return {pending_container:container.id};
 }
 if(p==='threads'){
  const id=e.THREADS_USER_ID,t=e.THREADS_ACCESS_TOKEN;
  const b=d.type==='image'?{media_type:'IMAGE',image_url:d.url,text:d.text}:
    d.type==='video'?{media_type:'VIDEO',video_url:d.url,text:d.text}:
    {media_type:'TEXT',text:d.type==='link'?d.text+'\n'+d.url:d.text};
  const c=await call(THREADS,id+'/threads',t,b);
  return {pending_container:c.id};
 }
 throw Error('Unsupported platform');
}
async function finishPending(e,p,container){
 const base=p==='instagram'?API:THREADS;
 const token=p==='instagram'?e.IG_ACCESS_TOKEN:e.THREADS_ACCESS_TOKEN;
 const id=p==='instagram'?e.IG_USER_ID:e.THREADS_USER_ID;
 const u=new URL(base+'/'+container);
 u.searchParams.set('fields','status_code');
 const r=await fetch(u,{headers:{Authorization:'Bearer '+token}});
 const j=await r.json().catch(()=>({}));
 if(!r.ok)throw Error((j.error?.message||'Container status failed').slice(0,180));
 if(j.status_code==='ERROR'||j.status_code==='EXPIRED')throw Error('Media processing '+j.status_code);
 if(j.status_code!=='FINISHED')return null;
 return call(base,id+(p==='instagram'?'/media_publish':'/threads_publish'),token,{creation_id:container});
}
function validate(d){
 if(!d||!['text','link','image','video'].includes(d.type)||!d.text?.trim())throw Error('اكتب نص المنشور أولًا.');
 if(d.type!=='text'&&(!/^https:\/\//.test(d.url||'')))throw Error('الرابط يجب أن يبدأ https://');
 if(d.text.length>500)throw Error('النص أكبر من 500 حرف؛ قلله لتوافق Threads.');
}
export async function socialCommand(e,raw){
 const [command,...rest]=raw.trim().split(/\s+/);const arg=rest.join(' ').trim();
 const k=key(e);let d=await read(e,k);
 if(command==='/social')return message(e,'إدارة المنشورات:\n/post نص المنشور\n/postimage رابط_صورة_HTTPS | النص\n/postvideo رابط_فيديو_HTTPS | النص\n/postlink رابط_HTTPS | النص\n/targets facebook,instagram,threads\n/postreview\n/postpublish تأكيد\n/postschedule YYYY-MM-DDTHH:MMZ\n/postscheduleconfirm تأكيد\n/poststatus\n/postlist\n/postcancel\nمهم: المنشورات لا تُنشر قبل التأكيد. استخدم روابط وسائط عامة مباشرة.');
 if(command==='/post'||['/postimage','/postvideo','/postlink'].includes(command)){
  const type=command==='/post'?'text':command.slice(5)==='image'?'image':command.slice(5)==='video'?'video':'link';
  const parts=type==='text'?['',arg]:arg.split(/\s*\|\s*/);
  if(parts.length!==2)throw Error('استخدم: '+command+' رابط_HTTPS | نص المنشور');
  d={id:crypto.randomUUID(),type,url:parts[0],text:parts[1],targets:[],state:'draft',created_at:new Date().toISOString()};
  validate(d);await save(e,k,d);await storeItem(e,d);return message(e,'تم حفظ المسودة. اختر المنصات عبر /targets facebook,instagram,threads ثم /postreview');
 }
 if(command==='/poststatus')return message(e,d?JSON.stringify({id:d.id,state:d.state,targets:d.targets,results:d.results||{},due:d.due||null}):'لا توجد مسودة.');
 if(command==='/postcancel'){if(d&&!['draft','scheduled','pending_schedule'].includes(d.state))throw Error('لا يمكن إلغاء طلب بدأ نشره.');if(d){d.state='cancelled';await storeItem(e,d);}await e.BOT_STATE.delete(k);return message(e,'تم إلغاء المسودة غير المنشورة.');}
 if(!d)throw Error('أنشئ مسودة أولًا عبر /post أو /postimage أو /postvideo.');
 if(command==='/targets'){
  if(d.state!=='draft')throw Error('لا يمكن تعديل طلب بدأ نشره.');
  const selected=arg.toLowerCase().split(/[,\s]+/).filter(Boolean);
  if(!selected.length||selected.some(p=>!PLATFORMS.includes(p)))throw Error('اختر facebook,instagram,threads');
  d.targets=[...new Set(selected)];delete d.reviewed_at;validate(d);await save(e,k,d);await storeItem(e,d);
  return message(e,'المنصات: '+d.targets.map(p=>p+(supported(e,p)?' متصل':' يحتاج ربط')).join('، '));
 }
 if(command==='/postreview'){
  if(d.state!=='draft')throw Error('لا يمكن مراجعة طلب بدأ نشره أو تمت جدولته.');
  validate(d);if(!d.targets.length)throw Error('اختر المنصات أولًا.');
  if(d.targets.includes('instagram')&&!['image','video'].includes(d.type))throw Error('Instagram يتطلب صورة أو فيديو.');
  d.reviewed_at=new Date().toISOString();await save(e,k,d);await storeItem(e,d);
  return message(e,'معاينة المنشور:\n'+d.text+'\n'+(d.url||'')+'\nالنوع: '+d.type+'\nالمنصات: '+d.targets.join(', ')+'\nلن ينشر دون /postpublish تأكيد');
 }
 if(command==='/postschedule'){
  if(d.state!=='draft')throw Error('الطلب لم يعد مسودة.');
  const ms=Date.parse(arg);if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?:Z|[+-]\d\d:\d\d)?$/.test(arg)||!Number.isFinite(ms)||ms<Date.now()+120000)throw Error('أدخل موعدًا مستقبليًا ISO 8601 مع المنطقة الزمنية.');
  if(!d.targets.length)throw Error('اختر المنصات أولًا.');
  if(!d.reviewed_at)throw Error('راجع المنشور أولًا عبر /postreview قبل الجدولة.');
  d.due=new Date(ms).toISOString();d.state='pending_schedule';await save(e,k,d);
  await storeItem(e,d);
  return message(e,'موعد الجدولة: '+d.due+'؛ للتأكيد النهائي اكتب /postscheduleconfirm تأكيد. لا يوجد نشر قبل التأكيد.');
 }
 if(command==='/postscheduleconfirm'){
  if(arg!=='تأكيد'||d.state!=='pending_schedule')throw Error('اكتب /postscheduleconfirm تأكيد بعد تحديد الموعد.');
  if(Date.parse(d.due)<Date.now()+60000)throw Error('انتهت صلاحية الموعد. أنشئ جدولة جديدة.');
  if(d.targets.some(p=>!supported(e,p)))throw Error('اربط بيانات اعتماد جميع المنصات قبل تأكيد الجدولة.');
  d.state='scheduled';await save(e,k,d);await storeItem(e,d);
  return message(e,'تم تأكيد الجدولة: '+d.due);
 }
 if(command==='/postpublish'){
  if(arg!=='تأكيد')throw Error('لتأكيد النشر اكتب /postpublish تأكيد');
  if(d.state!=='draft')throw Error('الطلب منشور أو قيد التنفيذ؛ لا تعيد الإرسال.');
  if(!d.targets.length)throw Error('اختر المنصات أولًا.');
  if(!d.reviewed_at)throw Error('راجع المنشور أولًا عبر /postreview قبل النشر.');
  if(d.targets.some(p=>!supported(e,p)))throw Error('اربط بيانات اعتماد جميع المنصات قبل تأكيد النشر.');
  return execute(e,d);
 }
 return false;
}
async function execute(e,d){
 validate(d);
 if(!d.targets.length)throw Error('No platforms selected');
 // Reserve before external calls. Never retry uncertain outcomes automatically.
 d.state='publishing';d.results=d.results||{};await storeItem(e,d);
 const currentDraft=await read(e,key(e));if(currentDraft?.id===d.id)await save(e,key(e),d);
 for(const p of d.targets){
  if(d.results[p])continue;
  d.results[p]={status:'in_flight',at:new Date().toISOString()};await storeItem(e,d);
  try{
   if(!supported(e,p))throw Error('Missing platform credentials');
   const result=await publishOne(e,p,d);
   d.results[p]=result.queued_external?{status:'queued_external',note:'GitHub Actions must confirm actual publication'}:result.pending_container?{status:'processing',container:result.pending_container}:{status:'published',id:result.id||result.post_id||null};
  }catch(err){d.results[p]={status:'needs_review',error:String(err.message||err).slice(0,200)};}
  await storeItem(e,d);
 }
 d.state=Object.values(d.results).every(x=>x.status==='published')?'published':'needs_review';
 await storeItem(e,d);const current=await read(e,key(e));if(current?.id===d.id)await save(e,key(e),d);
 return message(e,'نتائج المنشور '+d.id+':\n'+Object.entries(d.results).map(([p,v])=>p+': '+v.status+(v.error?' - '+v.error:'')).join('\n'));
}
export async function socialTick(e){
 const items=await listItems(e);
 let processed=0;
 for(const d of items){
  if(d.state==='scheduled'&&Date.parse(d.due)<=Date.now()){
   const latest=await read(e,itemKey(e,d.id));
   if(latest?.state!=='scheduled')continue;
   await execute(e,latest);processed++;continue;
  }
  if(d.state!=='needs_review'||!Object.values(d.results||{}).some(v=>v.status==='processing'))continue;
  let changed=false;
  for(const [p,v] of Object.entries(d.results)){
   if(v.status!=='processing')continue;
   try{
    const result=await finishPending(e,p,v.container);
    if(result){d.results[p]={status:'published',id:result.id||null};changed=true;}
   }catch(err){d.results[p]={status:'needs_review',error:String(err.message||err).slice(0,200)};changed=true;}
  }
  if(changed){
   d.state=Object.values(d.results).every(v=>v.status==='published')?'published':'needs_review';
   await storeItem(e,d);
   const current=await read(e,key(e));if(current?.id===d.id)await save(e,key(e),d);
   await message(e,'تحديث حالة المنشور '+d.id+': '+JSON.stringify(d.results));
   processed++;
  }
 }
 return 'processed:'+processed;
}
