// Stage 2: Telegram webhook for yt-auto-agent. Publish stays disabled.
// Cloudflare Secrets: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GITHUB_PAT, TELEGRAM_WEBHOOK_SECRET
// Cloudflare Var: GITHUB_REPO (owner/repo); KV binding: BOT_STATE.
const YT = /https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?[^\s]*v=|shorts\/|live\/)|youtu\.be\/)[^\s]+/i;
const MAX_MP4 = 20 * 1024 * 1024;
const MENU = [
  'لوحة وكيل يوتيوب — المرحلة الثانية',
  'أرسل رابط يوتيوب أو MP4 مع رابطه لحفظه فقط.',
  '/prepare — اقتراح مقاطع مرقمة فقط',
  '1 أو 1,3 أو /clip 1,3 — معاينة المقاطع المختارة فقط',
  '/seo — بيانات وصفية عند الطلب فقط',
  '/publish — متوقف حتى موافقتك على المعاينة',
  '/status — آخر مصدر',
  '/cancel — إلغاء المصدر والاقتراحات',
  '/menu — القائمة',
  'لا يوجد نشر تلقائي أو كابشنز داخل الفيديو في هذه المرحلة.'
].join('\n');

function getLink(m) { return ((m.text || m.caption || '').match(YT) || [])[0]?.replace(/[.,)]+$/, '') || ''; }
function getMp4(m) {
  const v = m.document || m.video;
  return v && ((v.file_name || '').toLowerCase().endsWith('.mp4') ||
    (v.mime_type || '').toLowerCase() === 'video/mp4') ? v : null;
}
async function telegram(env, method, data) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`,
    {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(data)});
  if (!r.ok) throw Error(`Telegram HTTP ${r.status}`);
  const j = await r.json();
  if (!j.ok) throw Error('Telegram API error');
  return j.result;
}
async function send(env, text) {
  await telegram(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID, text});
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
  const valid = new Set(plan.clips.map(c => c.id));
  if (ids.length > 5 || new Set(ids).size !== ids.length || ids.some(id => !valid.has(id)))
    throw Error('اختر أرقامًا مختلفة من القائمة الحالية، مثل 1,3');
  if (typeof plan.created_utc !== 'string' || plan.created_utc.length < 20)
    throw Error('قائمة اقتراحات غير صالحة. أرسل /prepare.');
  return ids;
}
async function dispatch(env, source, action, extra = {}) {
  const payload = {video_url: source.video_url, agent_action: action, ...extra};
  if (source.telegram_file_id) payload.telegram_file_id = source.telegram_file_id;
  const r = await github(env, '/dispatches', 'POST',
    {event_type: 'process_video', client_payload: payload});
  if (r.status !== 204) throw Error(`GitHub dispatch HTTP ${r.status}`);
}
async function processUpdate(env, update) {
  const m = update.edited_message || update.message;
  if (!m || String(m.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return;
  const text = (m.text || '').trim();
  const cmd = text.startsWith('/') ? text.split(/\s+/)[0].split('@')[0].toLowerCase() : '';
  const link = getLink(m);
  let media = getMp4(m);
  if (!media && link && m.reply_to_message) media = getMp4(m.reply_to_message);
  const key = `source:${env.TELEGRAM_CHAT_ID}`;
  const source = JSON.parse(await env.BOT_STATE.get(key) || '{}');

  if (['/menu', '/help', '/start'].includes(cmd)) return send(env, MENU);
  if (cmd === '/status') return send(env, 'آخر مصدر محفوظ: ' + (source.video_url || 'لا يوجد'));
  if (cmd === '/cancel') {
    await clearPlan(env);
    await env.BOT_STATE.put(key, '{}');
    return send(env, 'تم إلغاء المصدر والاقتراحات. لم يُنشر شيء.');
  }
  if (cmd === '/publish')
    return send(env, 'النشر متوقف حتى الموافقة الصريحة على المعاينة.');

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
        {clip_ids: ids.join(','), clip_plan_ref: plan.created_utc});
      return send(env, 'تم طلب معاينة المقاطع ' + ids.join(', ') + '. دون نشر.');
    } catch (e) {
      return send(env, String(e.message || e));
    }
  }
  if (cmd === '/prepare' || cmd === '/seo') {
    const selected = link ? {video_url: link, telegram_file_id: media?.file_id || ''} : source;
    if (!selected.video_url) return send(env, 'أرسل رابط يوتيوب أو MP4 مع رابط أولًا.');
    if (media && (media.file_size || 0) > MAX_MP4) return send(env, 'ملف MP4 أكبر من 20MB.');
    if (cmd === '/prepare') {
      await clearPlan(env);
      await env.BOT_STATE.put(key, JSON.stringify(selected));
    }
    await dispatch(env, selected, cmd.slice(1));
    return send(env, cmd === '/seo' ? 'تم طلب البيانات الوصفية فقط، دون نشر.' :
      'تم طلب اقتراح توقيتات فقط، دون قص أو نشر.');
  }
  if (link) {
    if (media && (media.file_size || 0) > MAX_MP4) return send(env, 'ملف MP4 أكبر من حد التنزيل 20MB.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, JSON.stringify(
      {video_url: link, telegram_file_id: media?.file_id || ''}));
    return send(env, 'تم حفظ المصدر فقط. استخدم /prepare لاقتراح التوقيتات.');
  }
  if (media) return send(env, 'وصل MP4 بدون رابط. أضف رابط يوتيوب في تعليق الملف أو رد عليه بالرابط.');
  if (text) return send(env, 'الأمر غير معروف. أرسل /menu.');
}
export default {
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
