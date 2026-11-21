// Telegram webhook: controlled preview, explicit approval and private YouTube publishing.
// Cloudflare Secrets: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GITHUB_PAT, TELEGRAM_WEBHOOK_SECRET
// Cloudflare Var: GITHUB_REPO (owner/repo); KV binding: BOT_STATE.
const YT = /https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?[^\s]*v=|shorts\/|live\/)|youtu\.be\/)[^\s]+/i;
const DRIVE = /^https:\/\/(?:www\.)?drive\.google\.com\/(?:file\/d\/[A-Za-z0-9_-]{15,}(?:\/|$)|(?:open|uc)\?id=[A-Za-z0-9_-]{15,}(?:&|$))/i;
const MAX_MP4 = 20 * 1024 * 1024;
function getLink(m) {
  const candidates = (m.text || m.caption || '').match(/https?:\/\/[^\s<>]+/gi) || [];
  for (const raw of candidates) {
    const link = raw.replace(/[.,)]+$/, '');
    if (YT.test(link) || DRIVE.test(link)) return link;
  }
  return '';
}
function getMp4(m) {
  const v = m.document || m.video;
  return v && ((v.file_name || '').toLowerCase().endsWith('.mp4') ||
    (v.mime_type || '').toLowerCase() === 'video/mp4') ? v : null;
}
async function telegram(env, method, data) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`,
    {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(data)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw Error('Telegram ' + method + ' HTTP ' + r.status + ': ' +
    String(j.description || 'API request failed').slice(0, 180));
  return j.result;
}
async function send(env, text, reply_markup = null) {
  await telegram(env, 'sendMessage', {
    chat_id: env.TELEGRAM_CHAT_ID, text,
    ...(reply_markup ? {reply_markup} : {})
  });
}
// Telegram keeps an older allowed_updates filter unless setWebhook changes it.
// Button clicks are callback_query updates; a message-only webhook silently drops them.
async function ensureButtonWebhook(env) {
  const info = await telegram(env, 'getWebhookInfo', {});
  if (!info || typeof info.url !== 'string' || !info.url.startsWith('https://'))
    throw Error('بوت تيليجرام ليس لديه Webhook HTTPS نشط.');
  const previous = info.allowed_updates;
  if (!Array.isArray(previous) || !previous.length ||
      previous.includes('callback_query')) {
    return {changed:false, enabled:true, errors:info.last_error_message || ''};
  }
  const updates = [...new Set([...previous, 'callback_query'])];
  const payload = {
    url: info.url, secret_token: env.TELEGRAM_WEBHOOK_SECRET,
    allowed_updates: updates, drop_pending_updates:false
  };
  if (Number.isInteger(info.max_connections) && info.max_connections >= 1 &&
      info.max_connections <= 100) payload.max_connections = info.max_connections;
  await telegram(env, 'setWebhook', payload);
  const confirmed = await telegram(env, 'getWebhookInfo', {});
  if (!Array.isArray(confirmed?.allowed_updates) ||
      !confirmed.allowed_updates.includes('callback_query')) {
    throw Error('لم يؤكد تيليجرام تفعيل استقبال ضغطات الأزرار.');
  }
  return {changed:true, enabled:true, errors:confirmed.last_error_message || ''};
}
async function ensureButtonsAndMenu(env, source) {
  let message = '🎛️ لوحة بوت الريلز';
  try {
    const state = await ensureButtonWebhook(env);
    message += state.changed ? '\n✅ تم تفعيل استقبال الأزرار في Telegram.' :
      '\n✅ إعداد استقبال الأزرار مفعّل.';
    if (state.errors) message += '\n⚠️ آخر خطأ Webhook: ' + String(state.errors).slice(0, 120);
  } catch (e) {
    message += '\n⚠️ تعذّر فحص Webhook: ' + String(e.message || e).slice(0, 130);
  }
  return showMenu(env, source, await readPlan(env), message);
}
async function fingerprint(value) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256',
    new TextEncoder().encode(String(value))));
  return [...bytes.slice(0, 6)].map(x => x.toString(16).padStart(2, '0')).join('');
}
async function sourceKey(source) {
  return fingerprint((source?.video_url || '') + '|' + (source?.telegram_file_id || ''));
}
async function planKey(plan) { return fingerprint(plan?.created_utc || ''); }
function inline(rows) { return {inline_keyboard: rows}; }
const clipDuration = clip => Math.round(clip.end_time - clip.start_time);
function previewIds(plan) {
  return Array.isArray(plan?.previewed_ids) ? plan.previewed_ids.filter(Number.isInteger) : [];
}
async function selection(env, plan) {
  const key = 'selection:' + String(env.TELEGRAM_CHAT_ID) + ':' + await planKey(plan);
  let entry = [];
  try { entry = JSON.parse(await env.BOT_STATE.get(key) || '[]'); } catch {}
  // Invalidate old checkbox selections when a batch has been uploaded.
  // IDs in older plans used to be stored as bare arrays.
  const generation = plan.last_published_utc || '';
  const ids = Array.isArray(entry) ? (generation ? [] : entry) :
    entry?.generation === generation ? entry.ids : [];
  const allowed = new Set((plan.clips || []).map(clip => clip.id));
  return Array.isArray(ids) ? ids.filter(id => Number.isInteger(id) && allowed.has(id)) : [];
}
async function setSelection(env, plan, ids) {
  const key = 'selection:' + String(env.TELEGRAM_CHAT_ID) + ':' + await planKey(plan);
  await env.BOT_STATE.put(key, JSON.stringify({
    ids, generation: plan.last_published_utc || ''
  }), {expirationTtl: 1209600});
}
function legacyOauthFailure(plan) {
  // The archived run 43767459446 failed at token refresh, before any upload.
  return plan?.status === 'publishing' &&
    plan.created_utc === '2026-11-15T21:37:01.857058+00:00' &&
    plan.preview_run_id === '43766799372' &&
    Array.isArray(plan.approved_ids) && plan.approved_ids.length === 1 &&
    plan.approved_ids[0] === 3 && !(plan.published_results || []).length;
}
function continuableClips(plan) {
  if (plan?.status !== 'published' || !Array.isArray(plan?.clips)) return [];
  const published = new Set((plan.published_results || [])
    .filter(item => Number.isInteger(item?.clip_id) &&
      /^✅/.test(item?.result || '') &&
      /https:\/\/youtu\.be\/[A-Za-z0-9_-]{11}/.test(item.result))
    .map(item => item.clip_id));
  // Refuse to restore a historical plan without verified upload results.
  if (!published.size) return [];
  return plan.clips.filter(clip => !published.has(clip.id));
}
async function resumePublishedPlan(env, source, plan) {
  if (!sameSource(source, plan)) throw Error('مصدر الريلز تغيّر.');
  const latest = await readPlan(env);
  if (latest?.status !== 'published' ||
      latest.created_utc !== plan.created_utc)
    throw Error('القائمة تغيرت. افتح /menu.');
  const remaining = continuableClips(latest);
  if (!remaining.length) throw Error('لا توجد ريلز متبقية موثقة من هذه القائمة.');
  latest.clips = remaining;
  latest.status = 'awaiting_selection';
  latest.previewed_ids = [];
  latest.preview_run_id = null;
  latest.approved_ids = [];
  latest.approval_utc = null;
  latest.last_published_utc = new Date().toISOString();
  latest.recommended_clip_id = remaining[0].id;
  await writePlan(env, latest);
  return latest;
}
function retriableAuthFailure(plan) {
  return (plan?.status === 'auth_failed' &&
      plan.auth_error_code === 'YOUTUBE_OAUTH_INVALID_GRANT' &&
      !(plan.published_results || []).length) || legacyOauthFailure(plan);
}

const PAGE_SIZE = 5;
const DEFAULT_OPTS = {captions:true,blur:'auto',privacy:'private',max_seconds:59};
function topicPlan(p) {return p?.method === 'topic_transcript_ai';}
function clipJob(p,id) {return p?.clip_jobs?.[String(id)]||null;}
function openClips(p) {return (p?.clips||[]).filter(c=>!['pending','queued','uploading','published','needs_review','dispatch_unknown'].includes(clipJob(p,c.id)?.state));}
async function preferences(env) {
  try {const o=JSON.parse(await env.BOT_STATE.get('settings:'+env.TELEGRAM_CHAT_ID)||'null');
    return {...DEFAULT_OPTS,...(o&&typeof o==='object'?o:{})};}
  catch {return {...DEFAULT_OPTS};}
}
async function updatePreference(env,key,value) {
  const o=await preferences(env);o[key]=value;
  await env.BOT_STATE.put('settings:'+env.TELEGRAM_CHAT_ID,JSON.stringify(o));return o;
}
function perClipSettings(p,clip,defaults) {
  return {...defaults,...((p.clip_settings||{})[String(clip.id)]||{})};
}
// Preview and publish use identical owner-selected rendering settings.
async function previewOptions(env, plan, ids) {
  const defaults = await preferences(env);
  return JSON.stringify(Object.fromEntries(ids.map(id => {
    const clip = plan.clips.find(c => c.id === id);
    if (!clip) throw Error('ريلز غير موجود ضمن القائمة.');
    const {captions, blur, privacy} = perClipSettings(plan, clip, defaults);
    return [String(id), {captions, blur, privacy}];
  })));
}
function videoTime(sec) {
  const n=Math.round(Number(sec)||0);
  const h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;
  return (h?String(h).padStart(2,'0')+':':'')+
    String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
}
function cairoTime(value) {
  return new Intl.DateTimeFormat('en-GB',{timeZone:'Africa/Cairo',
    dateStyle:'short',timeStyle:'short',hourCycle:'h23'}).format(new Date(value));
}
function cairoToUtc(value) {
  const m=String(value).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/);
  if(!m)throw Error('الوقت المطلوب: YYYY-MM-DD HH:MM بتوقيت القاهرة.');
  const [year,month,day,hour,minute]=m.slice(1).map(Number);
  if(month<1||month>12||day<1||day>31||hour>23||minute>59)throw Error('التاريخ غير صحيح.');
  const naive=Date.UTC(year,month-1,day,hour,minute);
  function formatted(ms) {
    const a=new Intl.DateTimeFormat('en-GB',{timeZone:'Africa/Cairo',
      year:'numeric',month:'2-digit',day:'2-digit',
      hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(ms));
    const n=k=>Number(a.find(p=>p.type===k)?.value);
    return Date.UTC(n('year'),n('month')-1,n('day'),n('hour'),n('minute'));
  }
  let utc=naive;
  for(let i=0;i<4;i++)utc+=naive-formatted(utc);
  if(formatted(utc)!==naive)throw Error('وقت غير صالح بسبب تغيير الساعة الصيفية.');
  if(utc<Date.now()+120000)throw Error('حدد موعدًا بعد دقيقتين على الأقل.');
  return new Date(utc).toISOString();
}
function spacingMinutes(value) {
  const m=String(value).match(/^(\d{1,5})(m|h|d)?$/i);
  if(!m)throw Error('اكتب الفاصل مثل 10m أو 1h أو 1d.');
  const v=Number(m[1])*({m:1,h:60,d:1440}[m[2]?.toLowerCase()||'m']);
  if(!Number.isInteger(v)||v<1||v>43200)throw Error('الفاصل من دقيقة إلى 30 يومًا.');
  return v;
}
async function currentPage(env,p) {
  const v=Number(await env.BOT_STATE.get('page:'+env.TELEGRAM_CHAT_ID+':'+await planKey(p)));
  return Number.isInteger(v)&&v>=0?v:0;
}
async function setCurrentPage(env,p,v) {
  await env.BOT_STATE.put('page:'+env.TELEGRAM_CHAT_ID+':'+await planKey(p),
    String(v),{expirationTtl:1209600});
}
async function topicControls(env,source,plan) {
  const h=await planKey(plan), chosen=await selection(env,plan);
  const page=await currentPage(env,plan),first=page*PAGE_SIZE,clips=plan.clips||[];
  const rows=[];
  for(const clip of clips.slice(first,first+PAGE_SIZE)) {
    const job=clipJob(plan,clip.id),state=job?.state||'';
    const icon=state==='published'?'✅':state==='pending'?'📅':
      ['queued','uploading'].includes(state)?'⏳':state==='needs_review'?'⚠️':
      chosen.includes(clip.id)?'☑️':'▫️';
    rows.push([{text:icon+' '+clip.id+'. '+(clip.reel_title||clip.title).slice(0,37)+
      ' · '+clipDuration(clip)+'ث',callback_data:'p:'+(state?'details':'toggle')+':'+h+':'+clip.id}]);
    rows.push([{text:'📄 عرض النص والتفاصيل '+clip.id,callback_data:'p:details:'+h+':'+clip.id}]);
  }
  const nav=[];
  if(page>0)nav.push({text:'◀️ السابق',callback_data:'p:page:'+h+':'+(page-1)});
  nav.push({text:(page+1)+'/'+Math.max(1,Math.ceil(clips.length/PAGE_SIZE)),callback_data:'p:menu:'+h});
  if(first+PAGE_SIZE<clips.length)nav.push({text:'التالي ▶️',callback_data:'p:page:'+h+':'+(page+1)});
  rows.push(nav);
  rows.push([{text:'☑️ تحديد المتاح كله',callback_data:'p:selectall:'+h},
             {text:'إلغاء التحديد',callback_data:'p:clear:'+h}]);
  rows.push([{text:'✅ اعتماد عناوين المحدد',callback_data:'p:titles:'+h}]);
  const withoutSeo=clips.filter(c=>c.seo_ready===false && !clipJob(plan,c.id));
  if(withoutSeo.length)rows.push([{text:'🔄 إعادة تجهيز SEO للمقاطع الناقصة ('+
    withoutSeo.length+')',callback_data:'p:fixseo:'+h}]);
  rows.push([{text:'🎞️ معاينة',callback_data:'p:preview:'+h},
             {text:'⚙️ مراجعة النشر',callback_data:'p:publishdirect:'+h}]);
  rows.push([{text:'📅 الجدولة',callback_data:'p:schedulehelp:'+h},
             {text:'⚙️ الإعدادات',callback_data:'p:settings:'+h}]);
  if(plan.schedule_draft)rows.push([{text:'📅 مراجعة الجدولة',
    callback_data:'p:startschedule:'+h}]);
  if(Object.values(plan.clip_jobs||{}).some(j=>j.state==='pending'))
    rows.push([{text:plan.schedule_paused?'▶️ استئناف الجدولة':'⏸️ إيقاف الجدولة',
      callback_data:'p:pause:'+h}]);
  rows.push([{text:'🔄 تحديث القائمة',callback_data:'p:menu:'+h}]);
  return inline(rows);
}
function topicListText(plan,page,chosen) {
  const clips=plan.clips||[];
  let t='🎬 الريلز: '+clips.length+' | المحدد: '+chosen.length+
    ' | منشور: '+clips.filter(c=>clipJob(plan,c.id)?.state==='published').length+
    ' | مجدول: '+clips.filter(c=>clipJob(plan,c.id)?.state==='pending').length+'\n';
  for(const c of clips.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE)) {
    const status=clipJob(plan,c.id)?.state||'';
    t+='\n'+(chosen.includes(c.id)?'☑️ ':'')+'مقطع '+c.id+
      ' | '+videoTime(c.end_time-c.start_time)+'\n'+(c.reel_title||c.title)+
      '\n'+videoTime(c.start_time)+' ← '+videoTime(c.end_time)+
      (status?' | '+status:'')+(c.seo_ready===false?' | SEO غير مكتمل':'')+'\n';
  }
  t+='\nافتح تفاصيل أي ريلز لقراءة النص الكامل وضبط المونتاج.';
  if(plan.schedule_paused)t+='\n⏸️ الجدولة متوقفة.';
  return t.slice(0,3750);
}
async function topicMenu(env,source,plan,title='🎛️ القائمة',messageId=null) {
  const page=await currentPage(env,plan),chosen=await selection(env,plan);
  return editOrSend(env,messageId,title+'\n'+topicListText(plan,page,chosen),
    await topicControls(env,source,plan));
}
async function readReelTranscript(env,plan,id) {
  const count=plan.transcript_chunks;
  if(!Number.isInteger(count)||count<1||count>16)throw Error('التفريغ الكامل غير محفوظ لهذا المصدر.');
  let text='';
  for(let i=0;i<count;i++) {
    const r=await github(env,'/actions/variables/TELEGRAM_REEL_TEXT_'+i);
    if(!r.ok)throw Error('تعذر جلب النص المحفوظ من GitHub.');
    text+=(await r.json()).value;
  }
  const zipped=Uint8Array.from(atob(text),c=>c.charCodeAt(0));
  const stream=new Blob([zipped]).stream().pipeThrough(new DecompressionStream('gzip'));
  const raw=await new Response(stream).arrayBuffer();
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))]
    .map(x=>x.toString(16).padStart(2,'0')).join('');
  if(hash!==plan.transcript_sha256)throw Error('فشل تطابق النص مع التفريغ الأصلي.');
  const payload=JSON.parse(new TextDecoder().decode(raw));
  if(payload.plan_ref!==plan.created_utc)throw Error('النص لا يخص الفيديو الحالي.');
  return payload.texts?.[String(id)]||'';
}
async function showReelDetails(env,plan,id) {
  const clip=plan.clips.find(c=>c.id===id);
  if(!clip)throw Error('المقطع غير موجود.');
  const config=perClipSettings(plan,clip,await preferences(env));
  const job=clipJob(plan,id),h=await planKey(plan);
  let msg='🎞️ مقطع '+id+' | رقم السلسلة '+clip.serial+'\n'+
    (clip.reel_title||clip.title)+'\n'+
    videoTime(clip.start_time)+' → '+videoTime(clip.end_time)+
    ' | المدة '+videoTime(clip.end_time-clip.start_time)+'\n'+
    'كابشنز: '+(config.captions?'نعم':'لا')+
    ' | بلور: '+config.blur+' | خصوصية: '+config.privacy+
    (job?'\nحالة النشر: '+job.state+(job.due_utc?' | '+cairoTime(job.due_utc):''):'')+
    '\n\nالنص الكامل:\n'+await readReelTranscript(env,plan,id)+
    '\n\nالوصف:\n'+(clip.reel_description||'SEO غير مكتمل')+
    '\n\nالكلمات المفتاحية:\n'+(clip.reel_tags_string||'غير جاهزة');
  for(let i=0;i<msg.length;i+=3500) {
    const markup=(!job&&i===0)?inline([
      [{text:'🔤 تغيير الكابشنز',callback_data:'p:caption:'+h+':'+id}],
      [{text:'🌫️ تغيير البلور',callback_data:'p:blur:'+h+':'+id},
       {text:'🔒 تغيير الخصوصية',callback_data:'p:privacy:'+h+':'+id}],
      [{text:'🎛️ القائمة',callback_data:'p:menu:'+h}]
    ]):null;
    await send(env,msg.slice(i,i+3500),markup);
  }
}


function activeJobs(plan) {
  return Object.values(plan?.clip_jobs||{}).some(j=>['pending','queued','uploading'].includes(j?.state));
}
async function settingsMenu(env,plan,messageId=null) {
  const o=await preferences(env),h=await planKey(plan);
  const rows=[
    [{text:'🔤 الكابشنز: '+(o.captions?'تشغيل':'إيقاف'),callback_data:'p:pref:'+h+':captions'}],
    [{text:'🌫️ البلور: '+o.blur,callback_data:'p:pref:'+h+':blur'}],
    [{text:'🔒 الخصوصية: '+o.privacy,callback_data:'p:pref:'+h+':privacy'}],
    [{text:'⏱️ أقصى مدة: '+o.max_seconds+'ث',callback_data:'p:pref:'+h+':max_seconds'}],
    [{text:'🎛️ رجوع',callback_data:'p:menu:'+h}]
  ];
  return editOrSend(env,messageId,
    '⚙️ الإعدادات الافتراضية للريلز الجديدة\nتغيّر بالضغط مباشرة، دون تأكيد آخر.\n'+
    'يمكن تخصيص كل مقطع من شاشة تفاصيله.\n'+
    'ملاحظة: أقصى مدة يطبّق عند تحليل الرابط التالي، لا يعيد قص المصدر الحالي.',
    inline(rows));
}
async function updateClipSetting(env,plan,id,key) {
  const clip=plan.clips.find(c=>c.id===id);
  if(!clip||clipJob(plan,id))throw Error('لا يمكن تغيير إعدادات مقطع بدأ نشره أو جدولتُه.');
  const o=perClipSettings(plan,clip,await preferences(env));
  const values=key==='captions'?[true,false]:
    key==='blur'?['auto','on','off']:['public','unlisted','private'];
  const idx=values.indexOf(o[key]);
  const next=values[(idx+1)%values.length];
  plan.clip_settings=plan.clip_settings||{};
  plan.clip_settings[String(id)]={...(plan.clip_settings[String(id)]||{}),[key]:next};
  await writePlan(env,plan);
  return next;
}
function selectedOpen(plan,chosen) {
  const eligible=new Set(openClips(plan).map(c=>c.id));
  return chosen.filter(id=>eligible.has(id));
}
async function reviewPublish(env,plan) {
  const d=plan.publish_review,h=await planKey(plan);
  if(!d||!d.ids?.length)throw Error('لا يوجد اختيار للمراجعة.');
  const rows=[
    [{text:'🌐 علني',callback_data:'p:reviewprivacy:'+h+':public'},
     {text:'🔗 غير مدرج',callback_data:'p:reviewprivacy:'+h+':unlisted'},
     {text:'🔒 خاص',callback_data:'p:reviewprivacy:'+h+':private'}],
    [{text:'🚀 فوري',callback_data:'p:reviewmode:'+h+':now'},
     {text:'📅 جدولة',callback_data:'p:reviewmode:'+h+':later'}]];
  if(d.mode==='later')rows.push(
    [{text:'بعد ساعة',callback_data:'p:reviewdate:'+h+':60'},
     {text:'غدًا',callback_data:'p:reviewdate:'+h+':1440'},
     {text:'بعد أسبوع',callback_data:'p:reviewdate:'+h+':10080'}],
    [{text:'10 دقائق',callback_data:'p:reviewgap:'+h+':10'},
     {text:'ساعة',callback_data:'p:reviewgap:'+h+':60'},
     {text:'يوم',callback_data:'p:reviewgap:'+h+':1440'}]);
  if(d.privacy&&d.mode&&(d.mode==='now'||d.start||d.items))
    rows.push([{text:'✅ تأكيد نهائي وتنفيذ',callback_data:'p:reviewconfirm:'+h}]);
  rows.push([{text:'↩️ إلغاء',callback_data:'p:menu:'+h}]);
  return send(env,'مراجعة المقاطع: '+d.ids.join('، ')+
    '\nالخصوصية: '+(d.privacy||'اختر')+
    '\nالنشر: '+(d.mode||'اختر')+
    (d.start?'\nالبداية: '+cairoTime(d.start):'')+
    (d.mode==='later'?'\nالفاصل: '+(d.gap||60)+' دقيقة':'')+
    '\nلا يبدأ الرفع قبل الضغط على التأكيد النهائي.',inline(rows));
}
async function queueTopicJobs(env,source,original,chosen,scheduled=null) {
  if(!topicPlan(original)||!sameSource(source,original))
    throw Error('الجدول لا يطابق مصدر الريلز.');
  const plan=await readPlan(env);
  if(!topicPlan(plan)||plan.created_utc!==original.created_utc)
    throw Error('القائمة تغيرت، افتح /menu.');
  const ids=selectedOpen(plan,chosen);
  if(!ids.length||ids.length!==chosen.length||ids.length>40||
      new Set(ids).size!==ids.length)
    throw Error('حدد ريلز لم يبدأ نشره أو جدولته.');
  const defaults=await preferences(env), now=new Date().toISOString();
  const states={...(plan.clip_jobs||{})};
  for(const id of ids) {
    const clip=plan.clips.find(c=>c.id===id);
    if(!clip?.seo_ready||clip?.reel_seo_version!==3||
       !clip.reel_title?.endsWith(' '+String(clip.serial)) ||
       !clip.reel_description||!clip.reel_tags_string)
      throw Error('بيانات SEO للريلز '+id+' غير مكتملة؛ لا يمكن رفعه.');
    const config=perClipSettings(plan,clip,defaults);
    if(!['public','private','unlisted'].includes(config.privacy) ||
       !['auto','on','off'].includes(config.blur) ||
       typeof config.captions!=='boolean')
      throw Error('إعدادات مونتاج أو خصوصية غير صالحة.');
    const at=scheduled?.[String(id)]||null;
    if(scheduled&&!at)throw Error('موعد ريلز '+id+' غير محدد.');
    if(at && (!Number.isFinite(Date.parse(at))||Date.parse(at)<Date.now()+60000))
      throw Error('وقت جدول ريلز '+id+' انتهى أو قريب جدًا.');
    states[String(id)]={
      state:at?'pending':'queued',approved_at:now,privacy:config.privacy,
      captions:config.captions,blur:config.blur,
      ...(at?{due_utc:at}:{}),
      ...(at?{}:{queued_at:now})
    };
    clip.title_approved=true;
  }
  plan.clip_jobs=states;
  delete plan.schedule_draft;
  await writePlan(env,plan);
  if(!scheduled) {
    try {
      await dispatch(env,plan,'direct_publish',{
        clip_ids:ids.join(','),clip_plan_ref:plan.created_utc
      });
    } catch (err) {
      // A failed HTTP response may arrive after GitHub has already accepted
      // the dispatch. Keep the clip locked so a second click cannot upload it twice.
      const latest=await readPlan(env);
      if(latest?.created_utc===plan.created_utc) {
        for(const id of ids) {
          const job=latest.clip_jobs?.[String(id)];
          if(job?.state==='queued') {
            job.state='dispatch_unknown';
            job.error=String(err?.message||err).slice(0,160);
          }
        }
        await writePlan(env,latest);
      }
      await send(env,'⚠️ تعذر التأكد من إرسال طلب الرفع إلى GitHub. '+
        'تم إيقاف إعادة المحاولة التلقائية حتى لا يتكرر النشر. راجع /status.');
      return;
    }
    return send(env,'🚀 تم اعتماد وإرسال أمر الرفع العلني/الخاص حسب إعداد كل ريلز: '+
      ids.join('، ')+'. بدون تأكيد ثانٍ. نتيجة كل رفع ستصل هنا.');
  }
  return send(env,'📅 تم اعتماد جدول '+ids.length+' ريلز. الرفع سيبدأ تلقائيًا في مواعيد القاهرة المحددة؛ '+ 
    'ابدأ بـ /menu لمتابعة الحالة. يمكنك /pause أو /resume أو /reschedule.');
}
async function scheduledTick(env,now=Date.now()) {
  const plan=await readPlan(env);
  if(!topicPlan(plan)||plan.schedule_paused||!activeJobs(plan))return 'none';
  const jobs=plan.clip_jobs||{};
  if(Object.values(jobs).some(j=>['needs_review','dispatch_unknown'].includes(j.state))) {
    return 'blocked_for_review';
  }
  const stale=Object.entries(jobs).find(([,j])=>['queued','uploading'].includes(j.state) &&
    Date.parse(j.queued_at||j.upload_started_at||0)<now-30*60*1000 &&
    !j.alerted);
  if(stale) {
    stale[1].alerted=true;
    await writePlan(env,plan);
    await send(env,'⚠️ ريلز '+stale[0]+' ما زال قيد التنفيذ منذ أكثر من 30 دقيقة. '+
      'لا يعاد رفعه تلقائيًا حتى لا يتكرر. راجع GitHub Actions وحالة يوتيوب.');
  }
  // One in-flight upload at a time; scheduled times are intended dispatch
  // starts, not an impossible guarantee of YouTube video readiness.
  if(Object.values(jobs).some(j=>['queued','uploading'].includes(j.state)))
    return 'busy';
  const pending=Object.entries(jobs).filter(([,j])=>j.state==='pending' &&
    j.due_utc && Date.parse(j.due_utc)<=now && j.approved_at)
    .sort((a,b)=>Date.parse(a[1].due_utc)-Date.parse(b[1].due_utc));
  if(!pending.length)return 'waiting';
  const [id]=pending[0];
  // Persist reservation *before* dispatch. If the dispatch response is
  // ambiguous we leave a locked state rather than risk a duplicate upload.
  jobs[id].state='queued';
  jobs[id].queued_at=new Date(now).toISOString();
  await writePlan(env,plan);
  try {
    await dispatch(env,plan,'direct_publish',{
      clip_ids:id,clip_plan_ref:plan.created_utc
    });
    return 'queued:'+id;
  } catch(e) {
    const latest=await readPlan(env);
    if(latest?.created_utc===plan.created_utc &&
       latest.clip_jobs?.[id]?.state==='queued') {
      latest.clip_jobs[id].state='dispatch_unknown';
      latest.clip_jobs[id].error=String(e.message||e).slice(0,180);
      await writePlan(env,latest);
    }
    await send(env,'⚠️ تعذّر التأكد من إرسال ريلز '+id+' للرفع. توقف تلقائيًا لمنع التكرار. راجع /status.');
    return 'dispatch_unknown:'+id;
  }
}

async function controls(env, source, plan) {
  if(topicPlan(plan)&&sameSource(source,plan))return topicControls(env,source,plan);
  const srcHash = await sourceKey(source);
  const basic = [];
  if (source?.video_url) {
    basic.push([{text: '🎬 تحليل واقتراح المقاطع', callback_data: 's:prepare:' + srcHash}]);
    basic.push([{text: '📝 بيانات السيو', callback_data: 's:seo:' + srcHash},
                {text: '📊 الحالة', callback_data: 's:status:' + srcHash}]);
  }
  if (!plan || !sameSource(source, plan) || !plan.clips?.length) {
    basic.push([{text: '🏠 القائمة', callback_data: 's:menu:' + srcHash}]);
    return inline(basic);
  }
  const hash = await planKey(plan);
  const rows = [];
  if (['awaiting_selection', 'previewed_awaiting_approval'].includes(plan.status)) {
    const chosen = await selection(env, plan);
    for (const clip of plan.clips) {
      rows.push([{text: `${chosen.includes(clip.id) ? '✅' : '▫️'} ${clip.id}. ${clip.title.slice(0, 37)} · ${clipDuration(clip)}ث`,
                  callback_data: `p:toggle:${hash}:${clip.id}`}]);
    }
    rows.push([
      {text: '🎞️ معاينة المحدد (' + chosen.length + ')', callback_data: 'p:preview:' + hash},
      {text: '🎞️ معاينة الكل (' + plan.clips.length + ')', callback_data: 'p:previewall:' + hash}
    ]);
    if (plan.status === 'previewed_awaiting_approval') {
      rows.push([
        {text: 'تحديد كل المعاينات', callback_data: 'p:selectall:' + hash},
        {text: 'إلغاء التحديد', callback_data: 'p:clear:' + hash}
      ]);
    }
    if (plan.status === 'previewed_awaiting_approval' && previewIds(plan).length) {
      rows.push([{text: '✅ اعتماد ونشر المحدد أو كل المعاينات',
                  callback_data: 'p:approvepublish:' + hash + ':' + plan.preview_run_id}]);
    }
  } else if (plan.status === 'approved') {
    rows.push([{text: '✅ بدء نشر المقطع المعتمد فورًا', callback_data: 'p:publish:' + hash}]);
  } else if (plan.status === 'published' && continuableClips(plan).length) {
    rows.push([{text: '🎬 أكمل الريلز المتبقية من نفس الفيديو',
                callback_data: 'p:continue:' + hash}]);
  } else if (retriableAuthFailure(plan)) {
    rows.push([{text: '🔑 إعادة المحاولة بعد تحديث OAuth', callback_data: 'p:retry:' + hash}]);
  }
  rows.push([{text: '📊 تحديث الحالة', callback_data: 'p:status:' + hash},
             {text: '🏠 القائمة', callback_data: 'p:menu:' + hash}]);
  if (!['publish_queued', 'publishing', 'published'].includes(plan.status)) {
    rows.push([{text: '❌ إلغاء المصدر', callback_data: 's:cancel:' + srcHash}]);
  }
  return inline(rows);
}
const LABELS = {
  awaiting_selection: 'اختر المقاطع ثم اضغط معاينة',
  previewed_awaiting_approval: 'المعاينة جاهزة — اعتماد واحد ينفذ النشر مباشرة',
  approved: 'معتمد، ولم يُنشر بعد',
  publish_queued: 'طلب النشر قيد التنفيذ',
  publishing: 'النشر قيد التنفيذ أو بحاجة لمراجعة نتيجة آخر تشغيل',
  auth_failed: 'فشل تفويض يوتيوب قبل الرفع، حدث OAuth ثم أعد المحاولة',
  published: 'تم تأكيد النشر'
};
async function editOrSend(env, messageId, text, markup) {
  if (Number.isInteger(messageId) && messageId > 0) {
    try {
      return await telegram(env, 'editMessageText', {
        chat_id: env.TELEGRAM_CHAT_ID, message_id: messageId,
        text, ...(markup ? {reply_markup: markup} : {})
      });
    } catch {
      // An old message may not be editable; send a fresh usable menu.
    }
  }
  return send(env, text, markup);
}
async function showMenu(env, source, plan, title = '🎛️ لوحة بوت الريلز', messageId = null) {
  if(topicPlan(plan)&&sameSource(source,plan))return topicMenu(env,source,plan,title,messageId);
  return editOrSend(env, messageId, title + '\n' +
    (source.video_url ? 'المصدر محفوظ. ' : 'أرسل رابط YouTube أو Google Drive للبدء. ') +
    (plan && sameSource(source, plan) ? (LABELS[plan.status] || 'الحالة: ' + plan.status) : ''),
    await controls(env, source, plan));
}
async function legacyPublishReview(env,plan) {
  const h=await planKey(plan),d=plan.legacy_publish_review;
  if(!d?.ids?.length)throw Error('لا توجد معاينات للاعتماد.');
  const rows=[[{text:'🌐 علني',callback_data:'p:legacyprivacy:'+h+':public'},
    {text:'🔗 غير مدرج',callback_data:'p:legacyprivacy:'+h+':unlisted'},
    {text:'🔒 خاص',callback_data:'p:legacyprivacy:'+h+':private'}]];
  if(d.privacy)rows.push([{text:'✅ تأكيد نهائي ورفع الآن',callback_data:'p:legacyconfirm:'+h}]);
  rows.push([{text:'↩️ رجوع',callback_data:'p:menu:'+h}]);
  return send(env,'مراجعة المقاطع: '+d.ids.join('، ')+
    '\nمستوى العرض: '+(d.privacy||'اختر أولًا')+
    '\nلن يبدأ النشر قبل التأكيد النهائي.',inline(rows));
}
async function publishPlan(env, source, plan) {
  if (!plan || plan.status !== 'approved' || !Array.isArray(plan.approved_ids) ||
      !plan.approved_ids.length || !plan.approval_utc)
    throw Error('النشر غير مسموح: اعتمد معاينة أولًا.');
  if (!sameSource(source, plan)) throw Error('المعاينة المعتمدة تخص مصدرًا آخر.');
  await requirePreviewArtifact(env, plan);
  plan.status = 'publish_queued';
  await writePlan(env, plan);
  try {
    await dispatch(env, plan, 'publish', {clip_ids: plan.approved_ids.join(','),
      clip_plan_ref: plan.created_utc,privacy:plan.publish_privacy||'private'});
  } catch (err) {
    plan.status = 'approved';
    await writePlan(env, plan);
    throw err;
  }
  return send(env, 'تم طلب نشر المقاطع: ' + plan.approved_ids.join(', ') +
    ' إلى يوتيوب فورًا. نتيجة الرفع ستصل عند اكتمال GitHub Actions؛ لن يُعاد إرسال نفس الطلب.', await controls(env, source, plan));
}
async function retryOauth(env, source, plan) {
  if (!sameSource(source, plan) || !retriableAuthFailure(plan) ||
      !Array.isArray(plan.approved_ids) || !plan.approved_ids.length ||
      !plan.approval_utc) throw Error('إعادة المحاولة غير مسموحة لهذه الحالة.');
  await requirePreviewArtifact(env, plan);
  plan.status = 'publish_queued';
  plan.auth_error_code = '';
  plan.retry_count = (plan.retry_count || 0) + 1;
  if (plan.retry_count > 3) throw Error('بلغت حد المحاولات. راجع الربط يدويًا.');
  await writePlan(env, plan);
  try {
    await dispatch(env, plan, 'publish', {clip_ids: plan.approved_ids.join(','),
      clip_plan_ref: plan.created_utc});
  } catch (err) {
    plan.status = 'auth_failed';
    plan.auth_error_code = 'YOUTUBE_OAUTH_INVALID_GRANT';
    await writePlan(env, plan);
    throw err;
  }
  return send(env, 'أُرسل طلب إعادة نشر نفس المعاينة المعتمدة بوضع خاص. جاري التحقق.',
    await controls(env, source, plan));
}
async function handleCallback(env, cb) {
  if (String(cb.message?.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) {
    console.warn('Callback rejected: chat mismatch');
    return;
  }
  if (String(cb.from?.id) !== String(env.TELEGRAM_OWNER_ID || env.TELEGRAM_CHAT_ID)) {
    await telegram(env, 'answerCallbackQuery', {callback_query_id: cb.id,
      text: 'الزر مخصص لمالك البوت فقط.', show_alert:true});
    console.warn('Callback rejected: user mismatch');
    return;
  }
  await telegram(env, 'answerCallbackQuery', {callback_query_id: cb.id});
  const data = String(cb.data || '');
  const pieces = data.split(':');
  if (pieces.length < 3 || !['p', 's'].includes(pieces[0])) return;
  const key = `source:${env.TELEGRAM_CHAT_ID}`;
  const source = JSON.parse(await env.BOT_STATE.get(key) || '{}');
  const plan = await readPlan(env);
  try {
    if (pieces[0] === 's') {
      if (pieces[2] !== await sourceKey(source))
        throw Error('هذه أزرار مصدر قديم. افتح /menu.');
      if (pieces[1] === 'menu' || pieces[1] === 'status')
        return showMenu(env, source, plan, pieces[1] === 'status' ? '📊 حالة المعالجة' : '🎛️ القائمة', cb.message?.message_id);
      if (pieces[1] === 'prepare' || pieces[1] === 'seo') {
        if (!source.video_url) throw Error('أرسل رابط فيديو أولًا.');
        if ((['publish_queued', 'publishing'].includes(plan?.status) || activeJobs(plan)))
          throw Error('عملية نشر قيد المتابعة. لا يمكن تغيير المصدر.');
        if (pieces[1] === 'prepare') {
          await clearPlan(env);
        }
        await dispatch(env, source, pieces[1]);
        return send(env, pieces[1] === 'prepare' ? '⏳ بدأ التحليل. ستصلك اقتراحات بأزرار.' :
          '⏳ بدأت معالجة بيانات السيو.', await controls(env, source, null));
      }
      if (pieces[1] === 'cancel') {
        if ((['publish_queued', 'publishing'].includes(plan?.status) || activeJobs(plan)))
          throw Error('النشر جارٍ أو يحتاج مراجعة؛ الإلغاء غير متاح.');
        return send(env, 'هل تريد حذف المصدر والاقتراحات؟',
          inline([[{text:'نعم، إلغاء',callback_data:'s:confirmcancel:'+pieces[2]}],
                  [{text:'رجوع',callback_data:'s:menu:'+pieces[2]}]]));
      }
      if (pieces[1] === 'confirmcancel') {
        if ((['publish_queued', 'publishing'].includes(plan?.status) || activeJobs(plan)))
          throw Error('النشر قيد التنفيذ، لا يمكن الإلغاء.');
        await clearPlan(env);
        await env.BOT_STATE.put(key, '{}');
        return showMenu(env, {}, null, 'تم إلغاء المصدر والاقتراحات.');
      }
      return;
    }
    if (!plan || pieces[2] !== await planKey(plan) || !sameSource(source, plan))
      throw Error('القائمة قديمة أو المصدر تغيّر. اطلب /menu.');

    if(topicPlan(plan)) {
      const action=pieces[1],id=Number(pieces[3]);
      if(action==='page') {
        const page=Number(pieces[3]);
        if(!Number.isInteger(page)||page<0||page>=Math.ceil(plan.clips.length/PAGE_SIZE))
          throw Error('رقم الصفحة غير صالح.');
        await setCurrentPage(env,plan,page);
        return showMenu(env,source,plan,'🎬 قائمة الريلز',cb.message?.message_id);
      }
      if(action==='details')return showReelDetails(env,plan,id);
      if(action==='settings')return settingsMenu(env,plan,cb.message?.message_id);
      if(action==='pref') {
        const key=pieces[3],allowed=['captions','blur','privacy','max_seconds'];
        if(!allowed.includes(key))throw Error('إعداد غير معروف.');
        const o=await preferences(env);
        const values=key==='captions'?[true,false]:
          key==='blur'?['auto','on','off']:
          key==='privacy'?['public','unlisted','private']:[59,180,30];
        await updatePreference(env,key,values[(values.indexOf(o[key])+1)%values.length]);
        return settingsMenu(env,plan,cb.message?.message_id);
      }
      if(['caption','blur','privacy'].includes(action)) {
        if(!Number.isInteger(id))throw Error('رقم ريلز غير صحيح.');
        const key={caption:'captions',blur:'blur',privacy:'privacy'}[action];
        const next=await updateClipSetting(env,plan,id,key);
        return send(env,'✅ تم ضبط '+key+' للريلز '+id+' على '+next+
          '. الإعداد محفوظ، دون إعادة تحليل أو نشر.');
      }
      if(action==='toggle') {
        if(!openClips(plan).some(c=>c.id===id))throw Error('هذا الريلز مجدول أو بدأ نشره.');
        const ids=await selection(env,plan);
        await setSelection(env,plan,ids.includes(id)?
          ids.filter(x=>x!==id):[...ids,id].sort((a,b)=>a-b));
        return showMenu(env,source,plan,'☑️ تحديث الاختيار',cb.message?.message_id);
      }
      if(action==='selectall'||action==='clear') {
        await setSelection(env,plan,action==='clear'?[]:openClips(plan).map(c=>c.id));
        return showMenu(env,source,plan,'☑️ تم تغيير التحديد',cb.message?.message_id);
      }
      if(action==='fixseo') {
        const ids=openClips(plan).filter(c=>c.seo_ready===false).map(c=>c.id);
        if(!ids.length)throw Error('كل الريلز لديها بيانات SEO جاهزة.');
        await dispatch(env,plan,'metadata',{
          clip_ids:ids.join(','),clip_plan_ref:plan.created_utc
        });
        return send(env,'🔄 بدأ استكمال SEO للريلز '+ids.join('، ')+
          ' من التفريغ المخزن، دون تحميل أو تحليل الفيديو مرة أخرى.');
      }
      if(action==='titles') {
        const selected=selectedOpen(plan,await selection(env,plan));
        const ids=selected.length?selected:openClips(plan).map(c=>c.id);
        if(!ids.length)throw Error('لا توجد عناوين جديدة لاعتمادها.');
        const latest=await readPlan(env);
        if(latest.created_utc!==plan.created_utc)throw Error('القائمة تغيرت.');
        for(const c of latest.clips)if(ids.includes(c.id))c.title_approved=true;
        await writePlan(env,latest);
        return showMenu(env,source,latest,'✅ تم اعتماد '+ids.length+
          ' عنوان. اختر النشر الفوري أو الجدولة في أي وقت.',cb.message?.message_id);
      }
      if(action==='publishdirect') {
        const ids=selectedOpen(plan,await selection(env,plan));
        if(!ids.length)throw Error('اختر الريلز المطلوب أولًا. يمكنك تحديد الكل بضغطة واحدة.');
        const latest=await readPlan(env);
        latest.publish_review={ids,mode:null,privacy:null};
        await writePlan(env,latest);
        return reviewPublish(env,latest);
      }
      if(action==='schedulehelp')return send(env,
        '📅 جدول الريلز (بتوقيت القاهرة):\n'+
        '/schedule 2026-11-22 18:00 10m\n'+
        'لجدولة المحدد بالتتابع كل 10 دقائق. مثال 1h كل ساعة، 1d كل يوم.\n'+
        '/schedule 3 2026-11-22 19:00\n'+
        'لتحديد موعد منفرد للريلز 3. يمكنك ضبط أكثر من ريلز فرديًا.\n'+
        'بعدها اضغط «اعتماد وتنفيذ الجدولة» مرة واحدة.\n'+
        '/reschedule 3 2026-11-23 18:00 لتعديل مجدول لم يبدأ.\n'+
        '/pause و /resume للتحكم في الجدول.',
        inline([[{text:'🎛️ القائمة',callback_data:'p:menu:'+await planKey(plan)}]]));
      if(action==='startschedule') {
        const draft=plan.schedule_draft;
        if(!draft||!Array.isArray(draft.ids)||!draft.ids.length)
          throw Error('لا توجد مواعيد محفوظة. اكتب /schedule أولًا.');
        const latest=await readPlan(env);
        latest.publish_review={ids:draft.ids,mode:'later',privacy:null,items:draft.items};
        await writePlan(env,latest);
        return reviewPublish(env,latest);
      }
      if(action==='pause') {
        const latest=await readPlan(env);
        if(latest.created_utc!==plan.created_utc||!activeJobs(latest))
          throw Error('لا توجد مهام مجدولة حالية.');
        latest.schedule_paused=!Boolean(latest.schedule_paused);
        await writePlan(env,latest);
        return showMenu(env,source,latest,latest.schedule_paused?
          '⏸️ أوقفت بدء أي رفع مجدول جديد. الرفع الجاري لا يمكن إلغاؤه.':
          '▶️ تم استئناف المهام المجدولة غير المنفذة.',cb.message?.message_id);
      }
      if(action==='preview') {
        const ids=selectedOpen(plan,await selection(env,plan));
        if(!ids.length)throw Error('اختر مقطعًا أو أكثر للمعاينة.');
        await dispatch(env,plan,'preview',{
          clip_ids:ids.join(','),clip_plan_ref:plan.created_utc,
          clip_options:await previewOptions(env,plan,ids)
        });
        return send(env,'🎞️ بدأ تجهيز '+ids.length+' معاينة، دون نشر.');
      }
      if(action==='approvepublish'||action==='approve') {
        if(plan.status!=='previewed_awaiting_approval' ||
           String(plan.preview_run_id)!==String(pieces[3]))
          throw Error('هذه معاينة قديمة؛ راجع القائمة.');
        const chosen=await selection(env,plan);
        const ids=selectedOpen(plan,chosen.length?chosen:previewIds(plan));
        if(!ids.length||!ids.every(x=>previewIds(plan).includes(x)))
          throw Error('لا توجد معاينات معتمدة صالحة.');
        const latest=await readPlan(env);
        latest.publish_review={ids,mode:null,privacy:null};
        await writePlan(env,latest);
        return reviewPublish(env,latest);
      }
      if(['reviewprivacy','reviewmode','reviewdate','reviewgap','reviewconfirm'].includes(action)) {
        const latest=await readPlan(env),draft=latest?.publish_review;
        if(!draft||latest.created_utc!==plan.created_utc)throw Error('المراجعة قديمة.');
        if(action==='reviewprivacy') {
          if(!['public','unlisted','private'].includes(pieces[3]))throw Error('خصوصية غير صالحة.');
          draft.privacy=pieces[3];
        } else if(action==='reviewmode') {
          if(!['now','later'].includes(pieces[3]))throw Error('اختيار غير صالح.');
          draft.mode=pieces[3];
          if(draft.mode==='now')delete draft.start;
        } else if(action==='reviewdate') {
          const v=Number(pieces[3]);
          if(![60,1440,10080].includes(v))throw Error('موعد غير صالح.');
          draft.start=new Date(Date.now()+v*60000).toISOString();
          delete draft.items;
        } else if(action==='reviewgap') {
          const v=Number(pieces[3]);
          if(![10,60,1440].includes(v))throw Error('فاصل غير صالح.');
          draft.gap=v;delete draft.items;
        } else {
          if(!draft.privacy||!draft.mode||(draft.mode==='later'&&!draft.start&&!draft.items))
            throw Error('اختر الخصوصية والموعد أولًا.');
          const ids=selectedOpen(latest,draft.ids);
          if(ids.length!==draft.ids.length)throw Error('حالة الريلز تغيرت.');
          latest.clip_settings=latest.clip_settings||{};
          for(const id of ids)latest.clip_settings[String(id)]={
            ...(latest.clip_settings[String(id)]||{}),privacy:draft.privacy};
          let scheduled=null;
          if(draft.mode==='later') {
            scheduled=draft.items||{};
            if(!draft.items)for(let i=0;i<ids.length;i++)
              scheduled[String(ids[i])]=new Date(Date.parse(draft.start)+i*(draft.gap||60)*60000).toISOString();
          }
          delete latest.publish_review;
          await writePlan(env,latest);
          return queueTopicJobs(env,source,latest,ids,scheduled);
        }
        await writePlan(env,latest);
        return reviewPublish(env,latest);
      }
    }

    if (pieces[1] === 'menu' || pieces[1] === 'status')
      return showMenu(env, source, plan, pieces[1] === 'status' ? '📊 حالة المعالجة' : '🎛️ القائمة', cb.message?.message_id);
    if (pieces[1] === 'continue') {
      const resumed = await resumePublishedPlan(env, source, plan);
      return showMenu(env, source, resumed,
        '🎬 الريلز المتبقية من نفس الفيديو — من غير إعادة تحليل.',
        cb.message?.message_id);
    }
    if (pieces[1] === 'toggle') {
      if (!['awaiting_selection', 'previewed_awaiting_approval'].includes(plan.status))
        throw Error('هذه القائمة لم تعد متاحة للتحديد.');
      const id = Number(pieces[3]);
      if (!Number.isInteger(id) || !plan.clips.some(c => c.id === id))
        throw Error('رقم مقطع غير صالح.');
      const ids = await selection(env, plan);
      const changed = ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id].sort((a,b)=>a-b);
      await setSelection(env, plan, changed);
      return showMenu(env, source, plan, 'اختياراتك: ' + (changed.join('، ') || 'لا يوجد'), cb.message?.message_id);
    }
    if (pieces[1] === 'selectall' || pieces[1] === 'clear') {
      if (!['awaiting_selection','previewed_awaiting_approval'].includes(plan.status))
        throw Error('لا يمكن تحديد مقاطع من هذه الحالة.');
      const all = plan.status === 'previewed_awaiting_approval' ?
        previewIds(plan) : plan.clips.map(c=>c.id);
      await setSelection(env, plan, pieces[1] === 'clear' ? [] : all);
      return showMenu(env, source, plan,
        pieces[1] === 'clear' ? 'تم إلغاء التحديد.' : 'تم تحديد كل المقاطع المتاحة.',
        cb.message?.message_id);
    }
    if (pieces[1] === 'preview' || pieces[1] === 'previewall') {
      const ids = pieces[1] === 'previewall' ?
        plan.clips.map(c=>c.id) : await selection(env, plan);
      if (!ids.length) throw Error('لا توجد مقاطع متبقية للمعاينة.');
      parseChoices(ids.join(','), plan);
      await dispatch(env, plan, 'preview',
        {clip_ids: ids.join(','), clip_plan_ref: plan.created_utc,
         clip_options: await previewOptions(env,plan,ids)});
      return send(env, 'بدأ تجهيز معاينة ' + ids.length + ' مقطع دون نشر. ستصلك البيانات مع كل فيديو.');
    }
    if (pieces[1] === 'approvepublish') {
      // One explicit owner action AFTER the video+SEO preview. Sending a link
      // can never cause a YouTube upload.
      if (plan.status !== 'previewed_awaiting_approval' || !previewIds(plan).length)
        throw Error('لا توجد معاينة جاهزة للموافقة والنشر.');
      if (pieces[3] !== String(plan.preview_run_id))
        throw Error('زر قديم. افتح القائمة لمعاينة النسخة الأحدث.');
      const chosen = await selection(env, plan);
      const ids = chosen.length ? chosen : previewIds(plan);
      if (!ids.length || !ids.every(id => previewIds(plan).includes(id) &&
                                        plan.clips.some(c => c.id === id)))
        throw Error('التحديد يتضمن مقطعًا لم تتم معاينته. حدد من المعاينات فقط.');
      await requirePreviewArtifact(env, plan);
      const latest = await readPlan(env);
      if (!latest || latest.status !== 'previewed_awaiting_approval' ||
          latest.created_utc !== plan.created_utc ||
          String(latest.preview_run_id) !== String(plan.preview_run_id) ||
          !ids.every(id => previewIds(latest).includes(id)))
        throw Error('هذه المعاينة اتعمدت أو اتغيرت بالفعل؛ لا تكرر الرفع.');
      latest.approved_ids = ids;
      latest.approval_utc = new Date().toISOString();
      latest.legacy_publish_review={ids,privacy:null};
      latest.approved_ids=[];
      latest.approval_utc=null;
      await writePlan(env,latest);
      return legacyPublishReview(env,latest);
    }
    if (pieces[1] === 'legacyprivacy') {
      if(!['public','unlisted','private'].includes(pieces[3]))throw Error('خصوصية غير صالحة.');
      const latest=await readPlan(env);
      if(!latest.legacy_publish_review)throw Error('مراجعة قديمة.');
      latest.legacy_publish_review.privacy=pieces[3];
      await writePlan(env,latest);
      return legacyPublishReview(env,latest);
    }
    if (pieces[1] === 'legacyconfirm') {
      const latest=await readPlan(env),review=latest.legacy_publish_review;
      if(!review?.privacy||latest.status!=='previewed_awaiting_approval')
        throw Error('اختر الخصوصية أولًا.');
      await requirePreviewArtifact(env,latest);
      latest.approved_ids=review.ids;
      latest.approval_utc=new Date().toISOString();
      latest.publish_privacy=review.privacy;
      latest.status='approved';
      delete latest.legacy_publish_review;
      await writePlan(env,latest);
      return publishPlan(env,source,latest);
    }
    if (pieces[1] === 'approve') {
      if (plan.status !== 'previewed_awaiting_approval' || !previewIds(plan).length)
        throw Error('لا توجد معاينة جاهزة للاعتماد.');
      if (pieces[3] !== String(plan.preview_run_id))
        throw Error('زر اعتماد قديم. افتح القائمة لمعاينة أحدث إصدار.');
      const selected = await selection(env, plan);
      const ids = selected.length ? selected : previewIds(plan);
      if (!ids.length || !ids.every(id => previewIds(plan).includes(id) &&
                                        plan.clips.some(c => c.id === id)))
        throw Error('اختر من المقاطع التي تمت معاينتها فقط.');
      await requirePreviewArtifact(env, plan);
      plan.status = 'approved';
      plan.approved_ids = ids;
      plan.approval_utc = new Date().toISOString();
      await writePlan(env, plan);
      return publishPlan(env, source, plan);
    }
    if (pieces[1] === 'publish') {
      if (plan.status !== 'approved') throw Error('لا توجد معاينة معتمدة.');
      return publishPlan(env, source, plan);
    }
    if (pieces[1] === 'confirm') return publishPlan(env, source, plan);
    if (pieces[1] === 'retry') {
      if (!retriableAuthFailure(plan)) throw Error('هذه المعاينة لا تحتاج لإعادة تفويض.');
      return editOrSend(env, cb.message?.message_id,
        'تأكد من تحديث OAuth وأن الفيديو غير منشور. هل تعيد رفع نفس المعاينة الخاصة؟',
        inline([[{text:'نعم، أعد المحاولة',callback_data:'p:confirmretry:'+pieces[2]}],
                [{text:'رجوع',callback_data:'p:menu:'+pieces[2]}]]));
    }
    if (pieces[1] === 'confirmretry') return retryOauth(env, source, plan);
  } catch (err) {
    return send(env, '⚠️ ' + String(err.message || err).slice(0, 250));
  }
}

async function github(env, pathname, method = 'GET', data) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${pathname}`, {
    method,
    headers: {authorization: `Bearer ${env.GITHUB_PAT}`, accept: 'application/vnd.github+json',
      'x-github-api-version': '2022-11-28', 'user-agent': 'yt-auto-agent-webhook',
      'content-type': 'application/json'},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})
  });
  return r;
}
async function readPlan(env) {
  const r = await github(env, '/actions/variables/TELEGRAM_CLIP_PROPOSALS');
  if (r.status === 404) return null;
  if (!r.ok) throw Error(`GitHub plan read HTTP ${r.status}`);
  let plan;
  try { plan = JSON.parse((await r.json()).value); }
  catch { throw Error('Invalid stored clip plan'); }
  return plan;
}
async function writePlan(env, plan) {
  const r = await github(env, '/actions/variables/TELEGRAM_CLIP_PROPOSALS',
    'PATCH', {value: JSON.stringify(plan)});
  if (r.status !== 204) throw Error(`GitHub plan update HTTP ${r.status}`);
}
async function requirePreviewArtifact(env, plan) {
  const run = String(plan.preview_run_id || '');
  if (!/^[0-9]+$/.test(run)) throw Error('المعاينة لم تُحفظ في GitHub بعد. أعد /clip.');
  const r = await github(env, `/actions/runs/${run}/artifacts`);
  if (!r.ok) throw Error(`GitHub preview artifact lookup HTTP ${r.status}`);
  const artifacts = (await r.json()).artifacts || [];
  if (!artifacts.some(a => a.name === `preview-${run}` && !a.expired))
    throw Error('ملف المعاينة غير جاهز أو انتهت صلاحيته. أعد المحاولة بعد اكتمال GitHub Actions.');
}
function sameSource(source, plan) {
  return source.video_url === plan.video_url &&
    (source.telegram_file_id || '') === (plan.telegram_file_id || '');
}
async function clearPlan(env) {
  const r = await github(env, '/actions/variables/TELEGRAM_CLIP_PROPOSALS');
  if (r.status === 404) return;
  if (!r.ok) throw Error(`GitHub plan read HTTP ${r.status}`);
  const changed = await github(env, '/actions/variables/TELEGRAM_CLIP_PROPOSALS',
    'PATCH', {value: '{}'});
  if (changed.status !== 204) throw Error(`GitHub plan invalidation HTTP ${changed.status}`);
}
function parseChoices(value, plan) {
  const text = String(value).trim().replace(/[٠-٩۰-۹]/g, digit => {
    const ar = '٠١٢٣٤٥٦٧٨٩';
    const fa = '۰۱۲۳۴۵۶۷۸۹';
    return String(ar.includes(digit) ? ar.indexOf(digit) : fa.indexOf(digit));
  });
  if (!/^[0-9]+(?:[\s,،.]+[0-9]+)*$/.test(text)) throw Error('صيغة الاختيار: 1 أو 1,3 أو /clip 1,3');
  const ids = text.split(/[\s,،.]+/).map(Number);
  if (!Array.isArray(plan?.clips) || !['awaiting_selection', 'previewed_awaiting_approval'].includes(plan?.status))
    throw Error('لا توجد اقتراحات متاحة. أرسل /prepare أولًا.');
  const valid = new Set((topicPlan(plan)?openClips(plan):plan.clips).map(c => c.id));
  if (ids.length > 40 || new Set(ids).size !== ids.length || ids.some(id => !valid.has(id)))
    throw Error('اختر أرقامًا مختلفة من القائمة الحالية، مثل 1,3');
  if (typeof plan.created_utc !== 'string' || plan.created_utc.length < 20)
    throw Error('قائمة اقتراحات غير صالحة. أرسل /prepare.');
  return ids;
}
async function dispatch(env, source, action, extra = {}) {
  const payload = {video_url: source.video_url, agent_action: action, ...extra};
  if(action==='prepare' && payload.max_seconds===undefined) {
    const current=await preferences(env);
    payload.max_seconds=current.max_seconds;
  }
  if (source.telegram_file_id) payload.telegram_file_id = source.telegram_file_id;
  const r = await github(env, '/dispatches', 'POST',
    {event_type: 'process_video', client_payload: payload});
  if (r.status !== 204) throw Error(`GitHub dispatch HTTP ${r.status}`);
}
async function processUpdate(env, update) {
  if (update.callback_query) return handleCallback(env, update.callback_query);
  const m = update.edited_message || update.message;
  if (!m || String(m.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return;
  const text = (m.text || '').trim();
  const cmd = text.startsWith('/') ? text.split(/\s+/)[0].split('@')[0].toLowerCase() : '';
  const link = getLink(m);
  let media = getMp4(m);
  if (!media && link && m.reply_to_message) media = getMp4(m.reply_to_message);
  const key = `source:${env.TELEGRAM_CHAT_ID}`;
  const source = JSON.parse(await env.BOT_STATE.get(key) || '{}');

  if (['/menu', '/help', '/start', '/fixbuttons'].includes(cmd))
    return ensureButtonsAndMenu(env, source);
  if (cmd === '/status') {
    const plan = await readPlan(env);
    return showMenu(env, source, plan, '📊 آخر مصدر: ' + (source.video_url || 'لا يوجد'));
  }
  if (cmd === '/cancel') {
    const existing = await readPlan(env);
    if ((['publish_queued', 'publishing'].includes(existing?.status) || activeJobs(existing)))
      return send(env, 'لا يمكن إلغاء نشر بدأ بالفعل. راجع /status وGitHub Actions.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, '{}');
    return send(env, 'تم إلغاء المصدر والاقتراحات. لم يُنشر شيء.');
  }

  if(cmd==='/settings') {
    const plan=await readPlan(env);
    if(!topicPlan(plan)||!sameSource(source,plan))
      return send(env,'إعدادات افتراضية: /captions on|off، /blur auto|on|off، '+
        '/privacy public|unlisted|private، /duration 30|59|180. ثم أرسل رابط الفيديو.');
    return settingsMenu(env,plan);
  }
  if(['/captions','/blur','/privacy','/duration'].includes(cmd)) {
    const args=text.slice(text.split(/\s+/)[0].length).trim().split(/\s+/);
    const key=cmd==='/captions'?'captions':cmd==='/duration'?'max_seconds':cmd.slice(1);
    const hasId=args.length===2&&/^\d+$/.test(args[0]);
    const raw=hasId?args[1]:args[0];
    const allowed=key==='captions'?['on','off']:
      key==='blur'?['auto','on','off']:
      key==='privacy'?['public','private','unlisted']:['30','59','180'];
    if(!allowed.includes(raw)||args.length!==(hasId?2:1))
      return send(env,'صيغة الإعداد: '+cmd+' '+(allowed.join('|'))+
        '، أو '+cmd+' رقم_الريلز القيمة.');
    const value=key==='captions'?raw==='on':key==='max_seconds'?Number(raw):raw;
    if(!hasId) {
      await updatePreference(env,key,value);
      return send(env,'✅ تم حفظ إعداد '+key+' افتراضيًا: '+raw);
    }
    const p=await readPlan(env),id=Number(args[0]);
    if(!topicPlan(p)||!sameSource(source,p))return send(env,'لا توجد قائمة ريلز نشطة.');
    if(!p.clips.some(c=>c.id===id)||clipJob(p,id))
      return send(env,'هذا الريلز غير موجود أو بدأ نشره/جدولته.');
    p.clip_settings=p.clip_settings||{};
    p.clip_settings[String(id)]={...(p.clip_settings[String(id)]||{}),[key]:value};
    await writePlan(env,p);
    return send(env,'✅ تم ضبط '+key+' للريلز '+id+' على '+raw+'، دون تغيير بقية المقاطع.');
  }
  if(cmd==='/title'||cmd==='/description'||cmd==='/tags') {
    const p=await readPlan(env);
    if(!topicPlan(p)||!sameSource(source,p))return send(env,'لا توجد ريلز قابلة لتعديل البيانات.');
    const match=text.match(/^\/(?:title|description|tags)(?:@\w+)?\s+(\d{1,3})\s+([\s\S]+)$/i);
    if(!match)return send(env,'الصيغة: '+cmd+' رقم_الريلز النص الجديد');
    const id=Number(match[1]),clip=p.clips.find(c=>c.id===id);
    if(!clip||clipJob(p,id))return send(env,'هذا المقطع منشور أو مجدول، ولا يُعدل من هنا.');
    const value=match[2].trim();
    if(cmd==='/title') {
      const old=clip.reel_title||clip.title;
      const suffix=old.match(/( #[\p{L}\p{N}_]+ \d+)$/u)?.[1]||' '+clip.serial;
      const base=value.replace(/( #[\p{L}\p{N}_]+ \d+)$/u,'').trim();
      const title=value.endsWith(' '+clip.serial)&&value.includes('#')?value:base+suffix;
      if(title.length>100||base.length<8||/https?:\/\//i.test(title)||!title.endsWith(' '+clip.serial))
        return send(env,'العنوان غير صالح: من 8 إلى 100 حرف، والرقم التسلسلي يجب أن يظل في نهايته.');
      clip.title=title;clip.reel_title=title;clip.title_approved=false;
    } else if(cmd==='/description') {
      const lines=value.split('|').map(x=>x.trim()).filter(Boolean);
      if(lines.length!==3||lines.some(x=>x.length<12))
        return send(env,'الوصف: 3 جمل مفصولة بعلامة | في رسالة واحدة.');
      const hashtags=(clip.reel_description||'').split('\n').at(-1);
      clip.reel_description=lines.join('\n')+'\n'+hashtags;
    } else {
      const tags=value.split(',').map(x=>x.trim());
      const tagCost=tags.reduce((sum,t)=>sum+t.length+(t.includes(' ')?2:0),0)+tags.length-1;
      if(value.length>500||tags.length<6||tags.length>20||
         tags.some(x=>!x||x.length>65||x.includes('#'))||
         new Set(tags.map(x=>x.toLowerCase())).size!==tags.length||
         tagCost>500)
        return send(env,'العلامات من 6 إلى 20 عبارة، ومجموعها مع فواصل يوتيوب لا يتجاوز 500 حرف. استخدم فاصلة إنجليزية واحدة ,');
      clip.reel_tags_string=value;
    }
    await writePlan(env,p);
    return send(env,'✅ تم تعديل بيانات الريلز '+id+' فقط. يمكنك مراجعتها في /menu.');
  }
  if(cmd==='/pause'||cmd==='/resume'||cmd==='/stop'||cmd==='/reschedule') {
    const p=await readPlan(env);
    if(!topicPlan(p)||!sameSource(source,p))return send(env,'لا يوجد جدول لهذا المصدر.');
    if(cmd==='/reschedule') {
      const match=text.match(/^\/reschedule\s+(\d+)\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)$/);
      if(!match)return send(env,'اكتب: /reschedule 3 2026-11-23 18:00');
      const id=Number(match[1]),job=p.clip_jobs?.[String(id)];
      if(!job||job.state!=='pending')return send(env,'يمكن تعديل موعد ريلز مجدول لم يبدأ فقط.');
      job.due_utc=cairoToUtc(match[2]+' '+match[3]);
      await writePlan(env,p);
      return send(env,'✅ موعد ريلز '+id+' الجديد: '+cairoTime(job.due_utc));
    }
    if(cmd==='/stop') {
      let n=0;
      for(const job of Object.values(p.clip_jobs||{}))
        if(job.state==='pending'){job.state='cancelled';n++;}
      delete p.schedule_draft;
      p.schedule_paused=true;
      await writePlan(env,p);
      return send(env,'⛔ تم إلغاء '+n+' رفع مجدول لم يبدأ. أي رفع جارٍ لا يمكن إلغاؤه.');
    }
    p.schedule_paused=cmd==='/pause';
    await writePlan(env,p);
    return send(env,p.schedule_paused?'⏸️ أوقفت بدء الرفع المجدول.':'▶️ استأنفت الجدولة.');
  }
  if(cmd==='/publishnow') {
    try {
      const p=await readPlan(env);
      if(!topicPlan(p)||!sameSource(source,p))
        return send(env,'لا توجد قائمة جاهزة للنشر المباشر.');
      const raw=text.slice(text.split(/\s+/)[0].length).trim();
      const ids=raw==='all'?openClips(p).map(c=>c.id):
        raw?parseChoices(raw,p):selectedOpen(p,await selection(env,p));
      const latest=await readPlan(env);
      latest.publish_review={ids,privacy:null,mode:null};
      await writePlan(env,latest);
      return reviewPublish(env,latest);
    } catch(e) {
      return send(env,'⚠️ '+String(e.message||e));
    }
  }

  if (cmd === '/schedule') {
    const plan = await readPlan(env);
    if (!plan || !sameSource(source, plan) ||
        !['awaiting_selection','previewed_awaiting_approval'].includes(plan.status))
      return send(env, 'لا توجد ريلز جاهزة لضبط موعد النشر. أرسل الرابط أولًا.');
    const args = text.slice(text.indexOf(' ') + 1).trim();
    if(topicPlan(plan)) {
      if(args.toLowerCase()==='off'){
        delete plan.schedule_draft;
        await writePlan(env,plan);
        return send(env,'تم إلغاء جدول المسودة دون المساس بريلز منشور أو مجدول.');
      }
      try {
        const individual=args.match(/^(\d{1,3})\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)$/);
        const batch=args.match(/^(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)\s+(\d{1,5}[mhd]?)$/i);
        if(!individual&&!batch)throw Error(
          'للجميع: /schedule 2026-11-22 18:00 10m (أو 1h أو 1d)\n' +
          'لفردي: /schedule 3 2026-11-22 19:00');
        const chosen=selectedOpen(plan,await selection(env,plan));
        const ids=individual?[Number(individual[1])]:
          chosen.length?chosen:openClips(plan).map(c=>c.id);
        if(!ids.length||!ids.every(id=>openClips(plan).some(c=>c.id===id)))
          throw Error('حدد مقاطع غير منشورة أو غير مجدولة فقط.');
        const items=individual?{...(plan.schedule_draft?.items||{})}:{};
        if(individual){
          items[String(ids[0])]=cairoToUtc(individual[2]+' '+individual[3]);
        } else {
          const first=Date.parse(cairoToUtc(batch[1]+' '+batch[2]));
          const spacing=spacingMinutes(batch[3]);
          const ordered=[...ids].sort((a,b)=>
            plan.clips.find(c=>c.id===a).start_time-
            plan.clips.find(c=>c.id===b).start_time);
          for(let i=0;i<ordered.length;i++)
            items[String(ordered[i])]=new Date(first+i*spacing*60000).toISOString();
        }
        plan.schedule_draft={ids:Object.keys(items).map(Number),items};
        await writePlan(env,plan);
        const summary=plan.schedule_draft.ids.sort((a,b)=>a-b).map(id=>
          'ريلز '+id+': '+cairoTime(items[String(id)])).join('\n');
        return send(env,'📅 معاينة جدول الرفع العلني/الخاص بتوقيت مصر:\n'+summary+
          '\nلن يبدأ أي رفع حتى تعتمد الجدول.',
          inline([[{text:'📅 مراجعة الجدولة',
            callback_data:'p:startschedule:'+await planKey(plan)}],
            [{text:'↩️ القائمة',callback_data:'p:menu:'+await planKey(plan)}]]));
      } catch(e){return send(env,'⚠️ '+String(e.message||e));}
    }
    if (args.toLowerCase() === 'off') {
      delete plan.schedule;
      await writePlan(env, plan);
      return send(env, 'تم إلغاء الجدولة. سيبقى الرفع خاصًا عند الاعتماد.');
    }
    const match = args.match(/^(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)(?:\s+(\d{1,3}))?$/);
    if (!match || !Number.isFinite(Date.parse(match[1]+'T'+match[2]+':00Z')) ||
        Number(match[3]||24) < 1 || Number(match[3]||24) > 168)
      return send(env, 'صيغة الجدولة بتوقيت مصر: /schedule 2026-11-20 18:00 24\nالرقم الأخير ساعات بين نشر الريلز المتعددة. /schedule off للإلغاء.');
    plan.schedule = {first_local: match[1]+' '+match[2],
                     spacing_hours: Number(match[3] || 24)};
    await writePlan(env, plan);
    return send(env, 'تم حفظ الموعد بمصر: '+plan.schedule.first_local+
      '؛ وعند اعتماد عدة ريلز يكون الفرق '+plan.schedule.spacing_hours+
      ' ساعة. لن يبدأ الرفع قبل الموافقة.');
  }
  if (cmd === '/recorded') {
    const plan = await readPlan(env);
    if (!plan || !sameSource(source, plan) ||
        !['awaiting_selection','previewed_awaiting_approval'].includes(plan.status))
      return send(env,'لا توجد ريلز حالية لتسجيل تاريخ التصوير.');
    const value = text.slice(text.indexOf(' ')+1).trim();
    if (value.toLowerCase() === 'off') delete plan.recording_date_override;
    else {
      // A date-only guess would invent a recording time; require full timezone.
      const date = Date.parse(value);
      if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)$/.test(value) ||
          !Number.isFinite(date) || date > Date.now())
        return send(env,'اكتب تاريخ التصوير المؤكد مع الساعة والمنطقة الزمنية، مثل: /recorded 2026-11-15T14:30:00+02:00 أو /recorded off.');
      plan.recording_date_override = value;
    }
    await writePlan(env, plan);
    return send(env,'تم تحديث تاريخ التسجيل الموثق للفيديو دون إعادة رفع.');
  }
  if (cmd === '/continue') {
    try {
      const plan = await readPlan(env);
      if (!plan) throw Error('لا يوجد فيديو محفوظ؛ أرسل رابط الفيديو.');
      if (!sameSource(source, plan))
        throw Error('القائمة لا تطابق الفيديو المحفوظ.');
      if (['publish_queued', 'publishing', 'approved'].includes(plan.status))
        return showMenu(env, source, plan,
          'المقطع المعتمد قيد الرفع الآن؛ لا تبدأ عملية ثانية لنفس المعاينة. '
          + 'عند اكتمال الرفع ستظهر بقية الريلز تلقائيًا. '
          + 'لو استمرت الحالة بعد انتهاء GitHub Actions راجع /status.');
      if (plan.status === 'awaiting_selection' ||
          plan.status === 'previewed_awaiting_approval')
        return showMenu(env, source, plan, '🎬 الريلز المتبقية من نفس الفيديو.');
      const resumed = await resumePublishedPlan(env, source, plan);
      return showMenu(env, source, resumed, '🎬 اختر الريلز التالي من نفس المصدر.');
    } catch (e) { return send(env, String(e.message || e)); }
  }
  if (cmd === '/approve') {
    try {
      const plan = await readPlan(env);
      if(topicPlan(plan)) {
        if(!sameSource(source,plan))throw Error('مصدر الريلز مختلف.');
        const raw=text.slice(text.split(/\s+/)[0].length).trim();
        const ids=raw==='all'?openClips(plan).map(c=>c.id):
          raw?parseChoices(raw,plan):selectedOpen(plan,await selection(env,plan));
        if(!ids.length)throw Error('حدد عناوين من القائمة أو اكتب /approve all.');
        const latest=await readPlan(env);
        if(latest.created_utc!==plan.created_utc)throw Error('القائمة تغيرت.');
        for(const c of latest.clips)if(ids.includes(c.id))c.title_approved=true;
        await writePlan(env,latest);
        return showMenu(env,source,latest,'✅ تم اعتماد '+ids.length+' عنوان فقط؛ لم يبدأ النشر.');
      }
      if (!plan || plan.status !== 'previewed_awaiting_approval')
        throw Error('لا توجد معاينة تنتظر الموافقة. استخدم /clip أولًا.');
      if (!sameSource(source, plan)) throw Error('المعاينة لا تطابق الفيديو الحالي.');
      const raw = text.slice(text.split(/\s+/)[0].length).trim();
      const ids = parseChoices(raw, plan);
      if (!ids.every(id => (plan.previewed_ids || []).includes(id)))
        throw Error('يمكن اعتماد مقاطع تمت معاينتها فقط.');
      await requirePreviewArtifact(env, plan);
      plan.legacy_publish_review={ids,privacy:null};
      await writePlan(env,plan);
      return legacyPublishReview(env,plan);
    } catch (e) { return send(env, String(e.message || e)); }
  }
  if (cmd === '/publish') {
    try {
      const plan=await readPlan(env);
      if(topicPlan(plan)) {
        const ids=selectedOpen(plan,await selection(env,plan));
        if(!ids.length)throw Error('اختر الريلز المطلوب من القائمة أو /publishnow all.');
        const latest=await readPlan(env);
        latest.publish_review={ids,privacy:null,mode:null};
        await writePlan(env,latest);
        return reviewPublish(env,latest);
      }
      return send(env,'اختر النشر من أزرار المراجعة بعد معاينة الفيديو.');
    }
    catch (e) { return send(env, String(e.message || e)); }
  }
  if (cmd === '/retry') {
    try { return await retryOauth(env, source, await readPlan(env)); }
    catch (e) { return send(env, String(e.message || e)); }
  }

  if (cmd === '/clip' || /^[0-9٠-٩۰-۹]+(?:[\s,،.]+[0-9٠-٩۰-۹]+)*$/.test(text)) {
    const choiceText = cmd === '/clip' ? text.slice(text.split(/\s+/)[0].length).trim() : text;
    try {
      const plan = await readPlan(env);
      if (!plan || !plan.clips) throw Error('لا توجد اقتراحات محفوظة. أرسل /prepare أولًا.');
      if (!source.video_url || source.video_url !== plan.video_url ||
          (source.telegram_file_id || '') !== (plan.telegram_file_id || ''))
        throw Error('الاقتراحات لا تطابق المصدر الحالي. أرسل /prepare.');
      const ids = parseChoices(choiceText, plan);
      await dispatch(env, plan, 'preview',
        {clip_ids: ids.join(','), clip_plan_ref: plan.created_utc,
         clip_options: await previewOptions(env,plan,ids)});
      return send(env, 'تم طلب معاينة المقاطع ' + ids.join(', ') + '. دون نشر.');
    } catch (e) {
      return send(env, String(e.message || e));
    }
  }
  if (cmd === '/prepare' || cmd === '/seo') {
    const selected = link ? {video_url: link, telegram_file_id: media?.file_id || ''} : source;
    if (!selected.video_url) return send(env, 'أرسل رابط يوتيوب أو رابط فيديو Google Drive أولًا.');
    if (media && (media.file_size || 0) > MAX_MP4) return send(env, 'ملف MP4 أكبر من 20MB.');
    if (cmd === '/prepare') {
      const existing = await readPlan(env);
      if ((['publish_queued', 'publishing'].includes(existing?.status) || activeJobs(existing)))
        return send(env, 'هناك نشر قيد التنفيذ؛ لا يمكن تغيير المصدر الآن.');
      await clearPlan(env);
      await env.BOT_STATE.put(key, JSON.stringify(selected));
    }
    await dispatch(env, selected, cmd.slice(1));
    return send(env, cmd === '/seo' ? 'تم طلب البيانات الوصفية فقط، دون نشر.' :
      'جارٍ تجهيز أفضل ريلز ومعاينته وبيانات SEO تلقائيًا؛ دون نشر.');
  }
  if (link) {
    const existing = await readPlan(env);
    if ((['publish_queued', 'publishing'].includes(existing?.status) || activeJobs(existing)))
      return send(env, 'هناك نشر قيد التنفيذ؛ لا يمكن تغيير المصدر الآن.');
    if (media && (media.file_size || 0) > MAX_MP4) return send(env, 'ملف MP4 أكبر من حد التنزيل 20MB.');
    const nextSource = {video_url: link, telegram_file_id: media?.file_id || ''};
    if (sameSource(nextSource, source) && !existing?.clips?.length &&
        Number(source.auto_prepare_requested_at) > 0 &&
        Date.now() - Number(source.auto_prepare_requested_at) < 10 * 60 * 1000)
      return send(env, 'الرابط ده بيتم تجهيزه بالفعل. المعاينة هتوصلك تلقائيًا؛ مفيش حاجة تانية مطلوبة.');
    if (sameSource(nextSource, existing || {}) &&
        ['awaiting_selection', 'previewed_awaiting_approval', 'approved'].includes(existing?.status))
      return showMenu(env,source,existing,'🎬 الفيديو ده محلل بالفعل؛ القائمة جاهزة.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, JSON.stringify({
      ...nextSource, auto_prepare_requested_at: Date.now()
    }));
    await dispatch(env, nextSource, 'prepare');
    return send(env, 'وصل الرابط. هيتم اختيار أفضل مقطع وتجهيز الفيديو وSEO تلقائيًا. مفيش نشر قبل موافقتك.');
  }
  if (media) {
    if ((media.file_size || 0) > MAX_MP4)
      return send(env, 'ملف MP4 أكبر من 20MB. استخدم رابط ملف Google Drive قابل للتنزيل بدلًا منه.');
    if (!media.file_id)
      return send(env, 'ملف MP4 لم يصل بمعرّف صالح. أعد إرسال الملف.');
    if (!source.video_url)
      return send(env, 'أرسل رابط يوتيوب أولًا، وبعده ملف MP4 مباشرة. مش محتاج تعيد الرابط.');
    const existing = await readPlan(env);
    if (['publish_queued', 'publishing'].includes(existing?.status) || activeJobs(existing))
      return send(env, 'هناك نشر قيد التنفيذ؛ لا يمكن تغيير المصدر الآن.');
    const nextSource = {video_url: source.video_url, telegram_file_id: media.file_id,
      auto_prepare_requested_at: Date.now()};
    if (sameSource(nextSource, source) && existing?.clips?.length)
      return showMenu(env, source, existing, '🎬 الملف ده محلل بالفعل؛ القائمة جاهزة.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, JSON.stringify(nextSource));
    await dispatch(env, nextSource, 'prepare');
    return send(env, '✅ وصل ملف MP4 وربطته تلقائيًا بآخر رابط محفوظ. '
      + 'بدأ تجهيز الريلز وSEO من الملف مباشرة، بدون تنزيل من يوتيوب. '
      + 'مش محتاج /prepare، ومفيش نشر قبل موافقتك.');
  }
  if (text) return showMenu(env, source, await readPlan(env), 'الأمر غير معروف؛ استخدم الأزرار.');
}
export default {
  async scheduled(controller,env,ctx) {
    if(!env.GITHUB_PAT||!env.GITHUB_REPO||!env.BOT_STATE) {
      console.error('REEL_CRON_CONFIG_MISSING');
      return;
    }
    try {
      const result=await scheduledTick(env,controller?.scheduledTime||Date.now());
      console.log('REEL_SCHEDULE_TICK:',result);
    } catch(e) {
      console.error('REEL_SCHEDULE_ERROR:',String(e.message||e));
      if(env.TELEGRAM_TOKEN&&env.TELEGRAM_CHAT_ID) {
        try {await send(env,'⚠️ تعذر فحص جدول الريلز: '+
          String(e.message||e).slice(0,160));}catch{}
      }
    }
  },
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('Not found', {status: 404});
    if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID || !env.GITHUB_PAT ||
        !env.GITHUB_REPO || !env.TELEGRAM_WEBHOOK_SECRET || !env.BOT_STATE)
      return new Response('Configuration error', {status: 503});
    if (request.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.TELEGRAM_WEBHOOK_SECRET)
      return new Response('Forbidden', {status: 403});
    let update;
    try { update = await request.json(); }
    catch { return new Response('Bad request', {status: 400}); }
    if (!Number.isInteger(update.update_id)) return new Response('Bad update', {status: 400});
    const id = `update:${update.update_id}`;
    if (await env.BOT_STATE.get(id)) return new Response('OK');
    try {
      await processUpdate(env, update);
      await env.BOT_STATE.put(id, '1', {expirationTtl: 86400});
      return new Response('OK');
    } catch (e) {
      console.error('Webhook processing failed:', String(e.message || e));
      return new Response('Retry later', {status: 503});
    }
  }
};
