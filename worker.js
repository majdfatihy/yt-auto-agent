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
  let ids = [];
  try { ids = JSON.parse(await env.BOT_STATE.get(key) || '[]'); } catch {}
  const allowed = new Set((plan.clips || []).map(clip => clip.id));
  return Array.isArray(ids) ? ids.filter(id => Number.isInteger(id) && allowed.has(id)) : [];
}
async function setSelection(env, plan, ids) {
  const key = 'selection:' + String(env.TELEGRAM_CHAT_ID) + ':' + await planKey(plan);
  await env.BOT_STATE.put(key, JSON.stringify(ids), {expirationTtl: 1209600});
}
function legacyOauthFailure(plan) {
  // The archived run 43767459446 failed at token refresh, before any upload.
  return plan?.status === 'publishing' &&
    plan.created_utc === '2026-11-15T21:37:01.857058+00:00' &&
    plan.preview_run_id === '43766799372' &&
    Array.isArray(plan.approved_ids) && plan.approved_ids.length === 1 &&
    plan.approved_ids[0] === 3 && !(plan.published_results || []).length;
}
function retriableAuthFailure(plan) {
  return (plan?.status === 'auth_failed' &&
      plan.auth_error_code === 'YOUTUBE_OAUTH_INVALID_GRANT' &&
      !(plan.published_results || []).length) || legacyOauthFailure(plan);
}
async function controls(env, source, plan) {
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
    rows.push([{text: '🎞️ معاينة المقاطع المختارة (' + chosen.length + ')',
                callback_data: 'p:preview:' + hash}]);
    if (plan.status === 'previewed_awaiting_approval' && previewIds(plan).length) {
      rows.push([{text: '✅ اعتماد المعاينة ' + previewIds(plan).join('،'),
                  callback_data: 'p:approve:' + hash + ':' + plan.preview_run_id}]);
    }
  } else if (plan.status === 'approved') {
    rows.push([{text: '🔒 تأكيد النشر الخاص', callback_data: 'p:publish:' + hash}]);
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
  previewed_awaiting_approval: 'المعاينة جاهزة — اختار اعتماد المقطع الصحيح',
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
  return editOrSend(env, messageId, title + '\n' +
    (source.video_url ? 'المصدر محفوظ. ' : 'أرسل رابط YouTube أو Google Drive للبدء. ') +
    (plan && sameSource(source, plan) ? (LABELS[plan.status] || 'الحالة: ' + plan.status) : ''),
    await controls(env, source, plan));
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
      clip_plan_ref: plan.created_utc});
  } catch (err) {
    plan.status = 'approved';
    await writePlan(env, plan);
    throw err;
  }
  return send(env, 'تم طلب نشر المقاطع: ' + plan.approved_ids.join(', ') +
    ' بوضع خاص فقط. النشر لم يكتمل بعد.', await controls(env, source, plan));
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
        if (['publish_queued', 'publishing'].includes(plan?.status))
          throw Error('عملية نشر قيد المتابعة. لا يمكن تغيير المصدر.');
        if (pieces[1] === 'prepare') {
          await clearPlan(env);
        }
        await dispatch(env, source, pieces[1]);
        return send(env, pieces[1] === 'prepare' ? '⏳ بدأ التحليل. ستصلك اقتراحات بأزرار.' :
          '⏳ بدأت معالجة بيانات السيو.', await controls(env, source, null));
      }
      if (pieces[1] === 'cancel') {
        if (['publish_queued', 'publishing'].includes(plan?.status))
          throw Error('النشر جارٍ أو يحتاج مراجعة؛ الإلغاء غير متاح.');
        return send(env, 'هل تريد حذف المصدر والاقتراحات؟',
          inline([[{text:'نعم، إلغاء',callback_data:'s:confirmcancel:'+pieces[2]}],
                  [{text:'رجوع',callback_data:'s:menu:'+pieces[2]}]]));
      }
      if (pieces[1] === 'confirmcancel') {
        if (['publish_queued', 'publishing'].includes(plan?.status))
          throw Error('النشر قيد التنفيذ، لا يمكن الإلغاء.');
        await clearPlan(env);
        await env.BOT_STATE.put(key, '{}');
        return showMenu(env, {}, null, 'تم إلغاء المصدر والاقتراحات.');
      }
      return;
    }
    if (!plan || pieces[2] !== await planKey(plan) || !sameSource(source, plan))
      throw Error('القائمة قديمة أو المصدر تغيّر. اطلب /menu.');
    if (pieces[1] === 'menu' || pieces[1] === 'status')
      return showMenu(env, source, plan, pieces[1] === 'status' ? '📊 حالة المعالجة' : '🎛️ القائمة', cb.message?.message_id);
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
    if (pieces[1] === 'preview') {
      const ids = await selection(env, plan);
      if (!ids.length) throw Error('اختار مقطعًا واحدًا على الأقل من الأزرار.');
      parseChoices(ids.join(','), plan);
      await dispatch(env, plan, 'preview',
        {clip_ids: ids.join(','), clip_plan_ref: plan.created_utc});
      return send(env, '⏳ بدأت معاينة: ' + ids.join('، ') + '. بدون نشر.');
    }
    if (pieces[1] === 'approve') {
      if (plan.status !== 'previewed_awaiting_approval' || !previewIds(plan).length)
        throw Error('لا توجد معاينة جاهزة للاعتماد.');
      if (pieces[3] !== String(plan.preview_run_id))
        throw Error('زر اعتماد قديم. افتح القائمة لمعاينة أحدث إصدار.');
      const ids = previewIds(plan);
      if (!ids.every(id => plan.clips.some(c => c.id === id)))
        throw Error('المعاينة لا تطابق المقاطع الحالية.');
      await requirePreviewArtifact(env, plan);
      plan.status = 'approved';
      plan.approved_ids = ids;
      plan.approval_utc = new Date().toISOString();
      await writePlan(env, plan);
      return showMenu(env, source, plan, '✅ تم اعتماد المقاطع ' + ids.join('، ') + '. لم تُنشر.', cb.message?.message_id);
    }
    if (pieces[1] === 'publish') {
      if (plan.status !== 'approved') throw Error('لا توجد معاينة معتمدة.');
      return editOrSend(env, cb.message?.message_id,
        'تأكيد نهائي: نشر المقاطع ' + plan.approved_ids.join('، ') +
        ' على يوتيوب بوضع خاص؟',
        inline([[{text:'نعم، انشر خاص',callback_data:'p:confirm:'+pieces[2]}],
                [{text:'إلغاء القرار',callback_data:'p:menu:'+pieces[2]}]]));
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
    if (['publish_queued', 'publishing'].includes(existing?.status))
      return send(env, 'لا يمكن إلغاء نشر بدأ بالفعل. راجع /status وGitHub Actions.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, '{}');
    return send(env, 'تم إلغاء المصدر والاقتراحات. لم يُنشر شيء.');
  }
  if (cmd === '/approve') {
    try {
      const plan = await readPlan(env);
      if (!plan || plan.status !== 'previewed_awaiting_approval')
        throw Error('لا توجد معاينة تنتظر الموافقة. استخدم /clip أولًا.');
      if (!sameSource(source, plan)) throw Error('المعاينة لا تطابق الفيديو الحالي.');
      const raw = text.slice(text.split(/\s+/)[0].length).trim();
      const ids = parseChoices(raw, plan);
      if (!ids.every(id => (plan.previewed_ids || []).includes(id)))
        throw Error('يمكن اعتماد مقاطع تمت معاينتها فقط.');
      await requirePreviewArtifact(env, plan);
      plan.approved_ids = ids;
      plan.approval_utc = new Date().toISOString();
      plan.status = 'approved';
      await writePlan(env, plan);
      return showMenu(env, source, plan, '✅ تم اعتماد المقاطع ' + ids.join('، ') + '.');
    } catch (e) { return send(env, String(e.message || e)); }
  }
  if (cmd === '/publish') {
    try { return await publishPlan(env, source, await readPlan(env)); }
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
        {clip_ids: ids.join(','), clip_plan_ref: plan.created_utc});
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
      if (['publish_queued', 'publishing'].includes(existing?.status))
        return send(env, 'هناك نشر قيد التنفيذ؛ لا يمكن تغيير المصدر الآن.');
      await clearPlan(env);
      await env.BOT_STATE.put(key, JSON.stringify(selected));
    }
    await dispatch(env, selected, cmd.slice(1));
    return send(env, cmd === '/seo' ? 'تم طلب البيانات الوصفية فقط، دون نشر.' :
      'تم طلب اقتراح توقيتات فقط، دون قص أو نشر.');
  }
  if (link) {
    const existing = await readPlan(env);
    if (['publish_queued', 'publishing'].includes(existing?.status))
      return send(env, 'هناك نشر قيد التنفيذ؛ لا يمكن تغيير المصدر الآن.');
    if (media && (media.file_size || 0) > MAX_MP4) return send(env, 'ملف MP4 أكبر من حد التنزيل 20MB.');
    await clearPlan(env);
    await env.BOT_STATE.put(key, JSON.stringify(
      {video_url: link, telegram_file_id: media?.file_id || ''}));
    return showMenu(env, {video_url: link, telegram_file_id: media?.file_id || ''}, null, '✅ تم حفظ المصدر، بدون تحليل أو نشر.');
  }
  if (media) return send(env, 'وصل MP4 بدون رابط. أضف رابط يوتيوب في تعليق الملف أو رد عليه بالرابط.');
  if (text) return showMenu(env, source, await readPlan(env), 'الأمر غير معروف؛ استخدم الأزرار.');
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
