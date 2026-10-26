/**
 * 管理员控制台：仪表盘 · 实时交易流（WebSocket /ws/admin）· 泳道视图 · 用户与钱包 · 服务注册表 · 投放 · 需求 · 订单 · 合同卡检索 · 回放。
 *
 *   ?replay=<run>         从 /api/admin/replay/<run> 取事件，按真实间隔（> 1.5 s 压到 1.5 s）用 JS 虚拟时钟推进，同一套渲染；不连 WebSocket
 *   &auto=1               隐藏登录与控制条、载入即播（录制用）
 *   &tab=stream|lanes     进哪个视图；&group=0 关掉按卡聚合
 *   ?script=<url>&src=<dir>  剧本模式（demo 录制）：事件、名字、订单详情都从 <dir>/{events,users,registry,orders}.json 读，不登录、不调 API；
 *                         <url> 的 steps 规定每一步的时点、推进到哪条事件（upto）、切哪个页签、字幕、打开哪张订单
 *
 * 所有动效按 performance.now() 算（common.js 的 Clock / Born），录制工具用假时钟逐帧推进。
 */
'use strict';
const Q = new URLSearchParams(location.search);
const SCRIPT = Q.get('script') || '';
const SRC = (Q.get('src') || '').replace(/\/$/, '');
let REPLAY = Q.get('replay') || (SCRIPT ? 'script' : '');
const AUTO = Q.get('auto') === '1';
const cases = new Cases();
const allEvents = [];
const seenSeq = new Set();
let grouped = REPLAY ? Q.get('group') !== '0' : false;
let filterText = '';
let ws = null, wsTimer = 0, statsTimer = 0, listTimer = 0;
let USERS = [], REGISTRY = [], ORDERS = [], SEED_NEEDS = [];
let touched = { c: null, at: 0 };
const R = { events: [], vt: [], idx: 0, total: 0, done: false };
const S = { steps: [], idx: 0 };          // 剧本模式：按时点执行的步

if (AUTO) document.body.classList.add('auto');

/* ── 登录 ──────────────────────────────────────────────────── */
$('btnLogin').onclick = async () => {
  try { await api('/api/admin/login', { password: $('adminPw').value }); await enter(); }
  catch (e) { toast(e.message, 'bad'); }
};
$('adminPw').onkeydown = e => { if (e.key === 'Enter') $('btnLogin').click(); };
$('btnLogout').onclick = async () => { await api('/api/auth/logout', {}); location.href = '/admin'; };

async function enter() {
  $('loginView').classList.add('hide'); $('mainView').classList.remove('hide'); $('btnLogout').classList.remove('hide');
  await loadNames();
  if (REPLAY) await startReplay(); else await startLive();
  showTab(Q.get('tab') || 'stream');
  requestAnimationFrame(loop);
}
async function loadNames() {
  try {
    const [u, r] = SRC ? await Promise.all([api(SRC + '/users.json'), api(SRC + '/registry.json')])
      : await Promise.all([api('/api/admin/users'), api('/api/admin/registry')]);
    USERS = u; REGISTRY = r.services || []; Names.seedUsers(USERS); Names.seedServices(REGISTRY);
    $('taxTag').textContent = '分类树 ' + (r.taxonomy || '—');
  } catch (e) { toast('名字映射加载失败：' + e.message, 'bad'); }
}

/* ── 页签 ──────────────────────────────────────────────────── */
const LOADERS = { users: loadUsers, registry: loadRegistry, dispatch: loadDispatch, needs: loadNeeds, orders: loadOrders, runs: loadRuns };
function showTab(name) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + name));
  if (LOADERS[name]) LOADERS[name]();
  clearInterval(listTimer);
  if (!REPLAY && /users|registry|needs|orders/.test(name)) listTimer = setInterval(LOADERS[name], 5000);
}
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));

/* ── 仪表盘 ────────────────────────────────────────────────── */
function tile(k, v, cls, sub) { return `<div class="tile ${cls || ''}"><div class="k">${k}</div><div class="v">${v}${sub ? `<small>${sub}</small>` : ''}</div></div>`; }
function renderStats(s) {
  $('tiles').innerHTML = tile('在线助手 / 服务数', `${s.assistants ?? 0} <small>/ ${s.services ?? 0}</small>`, 'agent', s.runnable !== undefined ? `可运行 ${s.runnable}` : '') +
    tile('今日笔数', s.transfers_today ?? 0, '', s.volume_today !== undefined ? `${money(s.volume_today)} 虚拟币` : '') +
    tile('虚拟币流通量', money(s.circulating), 'pay') +
    tile('成交率', s.success_rate === null || s.success_rate === undefined ? '—' : `${Math.round(s.success_rate * 100)}%`, 'ok', `成 ${s.completed ?? 0}${s.declined ? ' · 拒 ' + s.declined : ''}`) +
    tile('平均成交时长', dur(s.avg_duration_s)) +
    tile('仲裁数', s.arbitrated ?? 0, 'market') +
    tile('模型调用 / token', `${s.llm_calls ?? 0}<small>${s.tokens ?? 0}</small>`) +
    tile('发现方式', OUTCOME[s.discovery] || s.discovery || '—', 'market', s.taxonomy ? `分类树 ${s.taxonomy}` : '');
  $('runTag').textContent = '运行 ' + (s.run || REPLAY || '—');
  $('modeTag').textContent = (s.mode || '') + ' 模式';
  $('discTag').textContent = OUTCOME[s.discovery] || s.discovery || '';
}
async function pollStats() {
  try { renderStats(await api('/api/admin/stats')); }
  catch (e) { if (e.status === 401) location.reload(); }
}
/** 回放：指标从事件推出来（同一套渲染，不读库） */
function replayStats() {
  const s = { assistants: 0, services: 0, runnable: undefined, transfers_today: 0, volume_today: 0, circulating: 0, completed: 0, arbitrated: 0, declined: 0, llm_calls: 0, tokens: 0, run: REPLAY, mode: '回放' };
  const asst = new Set(); let durs = [];
  allEvents.forEach(ev => {
    const k = Names.key(ev.sender), r = Names.key(ev.receiver);
    if (k.startsWith('asst-')) asst.add(k); if (r.startsWith('asst-')) asst.add(r);
    if (ev.op === 'service.register') s.services++;
    if (ev.op === 'seed.services') s.services += parseInt(ev.outcome) || 0;
    if (ev.op === 'transfer') { s.transfers_today++; s.volume_today += Number(ev.amount) || 0; }
    if (ev.op === 'wallet.adjust') s.circulating += Number(ev.amount) || 0;
    if (ev.op === 'order.completed') s.completed++;
    if (ev.op === 'arbitration.ruling') s.arbitrated++;
    if (ev.op === 'order.declined') s.declined++;
    if (ev.op === 'discovery.search') s.discovery = ev.outcome;
  });
  s.assistants = asst.size;
  cases.list.forEach(c => { if (c.state === '已结清' && c.firstAt) durs.push(c.lastAt - c.firstAt); });
  const total = s.completed + s.arbitrated + s.declined;
  s.success_rate = total ? s.completed / total : null;
  s.avg_duration_s = durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : null;
  s.circulating = s.circulating.toFixed(2); s.volume_today = s.volume_today.toFixed(2);
  s.llm_calls = '—'; s.tokens = '';
  renderStats(s);
}

/* ── 事件流 ────────────────────────────────────────────────── */
function matches(ev) {
  if (!filterText) return true;
  const hay = [labelOf(ev), Names.of(ev.sender), Names.of(ev.receiver), ev.cardDigest || '', ev.op].join(' ').toLowerCase();
  return hay.includes(filterText);
}
function rowEl(ev) {
  const d = el('div', 'ev');
  d.dataset.seq = ev.seq;
  d.innerHTML = `<span class="t">${hms(ev.at)}</span><span class="lb ${esc(ev.kind)}">${esc(labelOf(ev))}</span>
    <span class="ellipsis">${Names.html(ev.sender)}<span class="arrow">→</span>${Names.html(ev.receiver)}</span>
    <span class="cd">${esc(shortCd(ev.cardDigest))}</span><span class="mute">${ev.period ? esc(ev.period) : ''}</span>
    <span class="amt">${ev.amount ? money(ev.amount) : ''}</span><span class="out ${outcomeClass(ev.outcome)}" title="${esc(ev.outcome)}">${esc(outcomeOf(ev))}</span>`;
  d.onclick = () => {
    const old = d.querySelector('.detail');
    if (old) { old.remove(); d.classList.remove('sel'); return; }
    const info = { op: ev.op, kind: ev.kind, sender: ev.sender, receiver: ev.receiver, cardDigest: ev.cardDigest, period: ev.period, amount: ev.amount, outcome: ev.outcome, ...ev.detail };
    d.appendChild(el('div', 'detail', esc(JSON.stringify(info, null, 1)))); d.classList.add('sel');
  };
  Born.add(d);
  return d;
}
function groupHeader(c) {
  const who = c.isPlatform ? '<span class="who platform"><i></i>平台事件</span>'
    : `${Names.html(c.assistant)}<span class="arrow">→</span>${c.provider ? Names.html(c.provider) : '<span class="mute">（发现中）</span>'}`;
  return `<span class="caret">${c.el && c.el.classList.contains('open') ? '▾' : '▸'}</span><span class="cd">${c.cd ? shortCd(c.cd) : '—'}</span><span class="ellipsis" style="min-width:260px">${who}</span>
    ${c.isPlatform ? '' : sigChainHtml(c.sigs, { periods: c.periodMax || 1 })}
    ${c.phase >= 0 ? `<span class="chip on"><i></i>${PHASES[c.phase]}</span>` : ''}${c.state ? `<span class="badge ${c.stateClass}">${esc(c.state)}</span>` : ''}
    <span class="n">${c.events.length} 条${c.paid ? ` · 过账 ${c.paid.toFixed(2)}` : ''}</span>`;
}
function groupEl(c) {
  const g = el('div', 'group');
  g.dataset.key = c.key;
  const h = el('div', 'gh'); const b = el('div', 'gb');
  h.onclick = () => { g.classList.toggle('open'); h.innerHTML = groupHeader(c); };
  g.appendChild(h); g.appendChild(b);
  c.el = g; h.innerHTML = groupHeader(c);
  Born.add(g);
  return g;
}
function addToStream(ev, c, isNew) {
  const body = $('streamBody');
  if (!grouped) {
    if (!matches(ev)) return;
    body.insertBefore(rowEl(ev), body.firstChild);
    while (body.children.length > 500) body.lastChild.remove();
    return;
  }
  if (!c.el || !c.el.isConnected) { body.insertBefore(groupEl(c), body.firstChild); if (REPLAY && AUTO && !c.isPlatform) c.el.classList.add('open'); }
  else if (body.firstChild !== c.el) body.insertBefore(c.el, body.firstChild);
  if (matches(ev)) c.el.querySelector('.gb').appendChild(rowEl(ev));
  c.el.querySelector('.gh').innerHTML = groupHeader(c);
  if (REPLAY && AUTO && !c.isPlatform) {          // 录制：只展开当前活跃的卡，别的收起
    cases.list.forEach(x => { if (x.el && x !== c && x.el.classList.contains('open')) { x.el.classList.remove('open'); x.el.querySelector('.gh').innerHTML = groupHeader(x); } });
    if (!c.el.classList.contains('open')) { c.el.classList.add('open'); c.el.querySelector('.gh').innerHTML = groupHeader(c); }
  }
}
function rebuildStream() {
  $('streamBody').innerHTML = '';
  cases.list.forEach(c => { c.el = null; });
  Born.list.length = 0;
  allEvents.forEach(ev => addToStream(ev, (ev.cardDigest && cases.byCard[ev.cardDigest]) || findCase(ev), false));
  $('streamBody').querySelectorAll('.ev, .group').forEach(n => { n.style.opacity = 1; });
  Born.list.length = 0;
}
function findCase(ev) { return cases.list.find(c => c.events.includes(ev)) || cases._platform(); }
$('btnGroup').onclick = () => { grouped = !grouped; $('btnGroup').classList.toggle('active', grouped); rebuildStream(); };
$('btnGroup').classList.toggle('active', grouped);
$('streamFilter').oninput = () => { filterText = $('streamFilter').value.trim().toLowerCase(); rebuildStream(); };

/* ── 泳道 ──────────────────────────────────────────────────── */
function laneInner(c) {
  const ph = PHASES.map((p, i) => `${i ? `<span class="sep${i <= c.phase ? ' past' : ''}"></span>` : ''}<span class="ph${i < c.phase ? ' past' : i === c.phase ? ' on' : ''}"><i>${i + 1}</i>${p}</span>`).join('');
  return `<div class="party"><span>${Names.html(c.assistant)}<span class="arrow">→</span>${c.provider ? Names.html(c.provider) : '<span class="mute">（发现中）</span>'}</span>
    <span class="cd">${c.cd ? esc(c.cd.slice(0, 7) + ':' + shortCd(c.cd) + '…') : '尚无合同卡'}${c.paid ? ` · 过账 ${c.paid.toFixed(2)}` : ''}</span></div>
    <div class="phases">${ph}</div>${sigChainHtml(c.sigs, { periods: c.periodMax || 1 })}<div class="st"><span class="badge ${c.stateClass}">${esc(c.state || '—')}</span></div>`;
}
function updateLane(c) {
  if (c.isPlatform || (!c.assistant && !c.cd)) return;
  if (!c.laneEl) { const ph = $('lanesEmpty'); if (ph) ph.remove(); c.laneEl = el('div', 'lane'); c.laneEl.dataset.key = c.key; $('lanes').appendChild(c.laneEl); Born.add(c.laneEl); }
  c.laneEl.innerHTML = laneInner(c);
}

/* ── 归并一条事件 ───────────────────────────────────────────── */
function ingest(ev) {
  normEvent(ev);
  if (ev.seq !== undefined) { if (seenSeq.has(ev.seq)) return; seenSeq.add(ev.seq); }
  allEvents.push(ev);
  const { c, isNew } = cases.ingest(ev);
  addToStream(ev, c, isNew);
  updateLane(c);
  touched = { c, at: Clock.now() };
  $('nStream').textContent = allEvents.length;
  $('nLanes').textContent = cases.list.filter(x => x.laneEl).length;
}

/* ── 实时：WebSocket ─────────────────────────────────────────── */
async function startLive() {
  await pollStats(); statsTimer = setInterval(pollStats, 3000);
  try { (await api('/api/admin/events?after=0&limit=1000')).forEach(ev => { ev.label = ev.label || labelOf(ev); ingest(ev); }); } catch (e) { /* 空运行 */ }
  $('streamBody').querySelectorAll('.ev, .group').forEach(n => { n.style.opacity = 1; }); Born.list.length = 0;
  connectWs();
}
function connectWs() {
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/admin';
  ws = new WebSocket(url);
  ws.onopen = () => { $('wsDot').className = 'dot'; $('wsText').textContent = '实时'; };
  ws.onmessage = m => { try { ingest(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  ws.onclose = () => { $('wsDot').className = 'dot off'; $('wsText').textContent = '已断开，3 秒后重连'; clearTimeout(wsTimer); wsTimer = setTimeout(connectWs, 3000); };
  ws.onerror = () => { try { ws.close(); } catch (e) { /* 已关 */ } };
}

/* ── 回放：虚拟时钟 ───────────────────────────────────────────── */
async function startReplay() {
  Clock.virtual = true;
  let evs, script = null;
  try {
    if (SCRIPT) { script = await api(SCRIPT); REPLAY = script.run || REPLAY; }
    evs = await api(SRC ? SRC + '/events.json' : '/api/admin/replay/' + encodeURIComponent(REPLAY));
  } catch (e) { $('mainView').classList.add('hide'); $('deniedView').classList.remove('hide'); $('deniedText').textContent = `读不到回放 ${REPLAY}：${e.message}`; return; }
  evs.forEach(normEvent);
  R.events = evs; R.vt = []; let acc = 0;
  if (script) scheduleScript(script);
  else {
    evs.forEach((ev, i) => { if (i) acc += Math.min(Math.max((ev.at || 0) - (evs[i - 1].at || 0), 0), 1.5); R.vt.push(acc * 1000); });
    R.total = acc * 1000 + 1500;
  }
  const m = /^(\d{4})(\d{2})(\d{2})/.exec(REPLAY);
  const date = m ? `${m[1]}-${m[2]}-${m[3]}` : (evs[0] ? new Date(evs[0].at * 1000).toISOString().slice(0, 10) : '');
  $('replayText').textContent = `回放 · 事件出自 ${date} 真实运行`; $('replayBadge').classList.remove('hide');
  $('wsBox').classList.add('hide'); $('ctl').classList.remove('hide');
  $('ctlNote').textContent = `${evs.length} 条事件 · 按真实间隔推进，间隔 > 1.5 s 压到 1.5 s`;
  replayStats();
  if (AUTO) Clock.play();
}
/** 剧本：每步 {t, dur, upto, tab, caption, order, close, title, focus}。upto 之前尚未进场的事件在这一步的窗口里匀速进场 */
function scheduleScript(sc) {
  S.steps = sc.steps || []; S.idx = 0;
  const seqs = R.events.map(ev => ev.seq);
  let from = 0;
  S.steps.forEach(st => {
    if (st.upto === undefined || st.upto === null) return;
    const to = seqs.findIndex(q => q > st.upto); const end = to < 0 ? seqs.length : to;
    const n = end - from, win = Math.max(st.dur - 900, 300), lead = Math.min(250, win);
    for (let i = from; i < end; i++) R.vt.push(st.t + lead + (n > 1 ? (win - lead) * (i - from) / (n - 1) : 0));
    from = end;
  });
  for (let i = from; i < seqs.length; i++) R.vt.push(Number.MAX_SAFE_INTEGER);   // 剧本没点到的事件不进场
  R.total = sc.total_ms || (S.steps.length ? S.steps[S.steps.length - 1].t + S.steps[S.steps.length - 1].dur : 0);
  $('ctlNote').textContent = `${S.steps.length} 步剧本 · ${R.events.length} 条事件`;
}
function runStep(st) {
  if (st.tab) showTab(st.tab);
  if (st.title !== undefined) showTitle(st.title);
  if (st.caption !== undefined) showCaption(st.caption);
  if (st.close) $('orderModal').classList.remove('on');
  if (st.order) openOrder(st.order);
  if (st.focus) { const g = document.querySelector(`#streamBody .group[data-key="${st.focus}"]`); if (g) $('streamBody').insertBefore(g, $('streamBody').firstChild); }
}
function showCaption(text) {
  const c = $('caption');
  if (!text) { c.classList.add('hide'); return; }
  c.textContent = text; c.classList.remove('hide'); Born.add(c, 300);
}
function showTitle(t) {
  const box = $('titleCard');
  if (!t) { box.classList.add('hide'); return; }
  box.innerHTML = `<div class="tag">${esc(t.tag || '')}</div><h1>${esc(t.main || '')}</h1><h2>${esc(t.sub || '')}</h2>`;
  box.classList.remove('hide'); Born.add(box, 600);
}
function stepReplay(n) {
  while (S.idx < S.steps.length && S.steps[S.idx].t <= n) runStep(S.steps[S.idx++]);
  while (R.idx < R.events.length && R.vt[R.idx] <= n) { ingest(R.events[R.idx++]); replayStats(); }
  if (R.idx >= R.events.length && n >= R.total && !R.done) { R.done = true; Clock.pause(); $('btnPlay').textContent = '▶ 重放'; }
  $('pos').textContent = `${(n / 1000).toFixed(1)} s / ${(R.total / 1000).toFixed(0)} s · ${R.idx}/${R.events.length}`;
  $('posBar').style.width = `${Math.min(n / R.total, 1) * 100}%`;
}
function resetReplay() {
  Clock.pause(); Clock.paused = 0; R.idx = 0; S.idx = 0; R.done = false;
  allEvents.length = 0; seenSeq.clear(); cases.list.length = 0; cases.byCard = {}; cases.pendingByAsst = {}; cases.platform = null;
  $('streamBody').innerHTML = ''; $('lanes').innerHTML = '<div class="empty" id="lanesEmpty">尚无合同卡</div>'; Born.list.length = 0; replayStats();
}
$('btnPlay').onclick = () => { if (R.done) resetReplay(); if (Clock.playing) { Clock.pause(); $('btnPlay').textContent = '▶ 播放'; } else { Clock.play(); $('btnPlay').textContent = '⏸ 暂停'; } };
$('btnAgain').onclick = () => { resetReplay(); Clock.play(); $('btnPlay').textContent = '⏸ 暂停'; };

/* ── 每帧 ──────────────────────────────────────────────────── */
function loop() {
  const n = Clock.now();
  if (REPLAY) stepReplay(n);
  animateBorn(n);
  const live = touched.c && n - touched.at < 1500 ? touched.c : null;
  cases.list.forEach(c => { [c.el, c.laneEl].forEach(x => { if (x) x.classList.toggle('live', c === live); }); });
  requestAnimationFrame(loop);
}

/* ── 用户与钱包 ───────────────────────────────────────────────── */
async function loadUsers() {
  if (!SRC) { try { USERS = await api('/api/admin/users'); Names.seedUsers(USERS); } catch (e) { return toast(e.message, 'bad'); } }
  $('usersTable').innerHTML = USERS.length ? `<table class="tbl"><tr><th>账号</th><th>手机号</th><th>备注</th><th class="num">余量</th><th>智能体</th><th>创建</th><th></th></tr>` +
    USERS.map(u => `<tr><td><code class="mute">${esc(u.id)}</code></td><td>${esc(u.phone)}${u.is_virtual ? ' <span class="badge">虚拟</span>' : ''}</td><td>${esc(u.note || '')}</td>
      <td class="num" style="color:var(--c-pay)">${money(u.balance)}</td><td>${(u.agents || []).map(a => `<span class="who ${a.role === 'assistant' ? 'agent' : 'provider'}"><i></i>${esc(a.name)}</span>`).join(' ')}</td>
      <td class="nowrap mute">${ymdhm(u.created)}</td><td class="nowrap"><button class="btn sm" data-w="${esc(u.id)}" data-d="1">发放</button> <button class="btn sm" data-w="${esc(u.id)}" data-d="-1">扣减</button></td></tr>`).join('') + '</table>'
    : '<div class="empty">还没有账号</div>';
  $('usersTable').querySelectorAll('[data-w]').forEach(b => b.onclick = async () => {
    const v = prompt(`${b.dataset.d === '1' ? '发放' : '扣减'}多少虚拟币？`, '10'); if (!v) return;
    const note = prompt('原因', '管理员调整') || '管理员调整';
    try { await api('/api/admin/wallet', { user_id: b.dataset.w, delta: String(Number(v) * Number(b.dataset.d)), note }); toast('已记录', 'ok'); loadUsers(); }
    catch (e) { toast(e.message, 'bad'); }
  });
}
$('btnUsersRefresh').onclick = loadUsers;
$('btnVu').onclick = async () => {
  const body = { count: Number($('vuCount').value) || 1, assistant: !!$('vuAsst').value.trim(), assistantName: $('vuAsst').value.trim(), credit: Number($('vuCredit').value) || 0 };
  try { const out = await api('/api/admin/users', body); toast(`已创建 ${out.length} 个虚拟用户`, 'ok'); await loadNames(); loadUsers(); }
  catch (e) { toast(e.message, 'bad'); }
};

/* ── 服务注册表 ───────────────────────────────────────────────── */
async function loadRegistry() {
  if (!SRC) {
    try { const r = await api('/api/admin/registry'); REGISTRY = r.services || []; Names.seedServices(REGISTRY); $('taxTag').textContent = '分类树 ' + (r.taxonomy || '—'); }
    catch (e) { return toast(e.message, 'bad'); }
  }
  renderRegistry();
}
function renderRegistry() {
  const f = $('regFilter').value.trim().toLowerCase();
  const rows = REGISTRY.filter(s => !f || (s.name + ' ' + (s.tags || []).join(' ') + ' ' + s.id).toLowerCase().includes(f));
  $('registryTable').innerHTML = rows.length ? `<table class="tbl"><tr><th>#</th><th>名称</th><th>领域标签</th><th>成约方式</th><th class="num">价</th><th>状态</th><th>信誉</th><th>服务方</th></tr>` +
    rows.map((s, i) => `<tr><td class="mute">${i + 1}</td><td><span class="who provider"><i></i>${esc(s.name)}</span>${s.runnable ? ' <span class="badge ok">可运行</span>' : ''}</td>
      <td>${(s.tags || []).map(t => `<span class="badge">${esc(t)}</span>`).join(' ')}</td><td>${esc(FORMATION[s.formation] || s.formation)}</td>
      <td class="num">${s.price !== null && s.price !== undefined ? esc(s.price) : '<span class="mute">报价</span>'}</td>
      <td><span class="badge ${s.status === 'online' ? 'ok' : s.status === 'busy' ? 'warn' : ''}">${esc(OUTCOME[s.status] || s.status)}</span></td>
      <td class="mono">${s.reputation ? `${s.reputation.completed} 单 · ${s.reputation.successRate === null ? '—' : Math.round(s.reputation.successRate * 100) + '%'}` : '—'}</td>
      <td><code class="mute">${esc(s.id)}</code></td></tr>`).join('') + '</table>' : '<div class="empty">注册表为空——到「投放」页注入种子</div>';
}
$('regFilter').oninput = renderRegistry; $('btnRegRefresh').onclick = loadRegistry;

/* ── 投放 ────────────────────────────────────────────────────── */
async function loadDispatch() {
  try { SEED_NEEDS = await api('/api/admin/seed-needs'); } catch (e) { SEED_NEEDS = []; }
  $('seedNeeds').innerHTML = SEED_NEEDS.length ? SEED_NEEDS.map((n, i) => `<label><input type="checkbox" data-i="${i}" checked><span class="ellipsis">${esc(n.text)}</span>
    <span>${Names.html(n.assistant_id)}</span><span class="mute">${esc(n.service || '')} · 预算 ${esc((n.scope || {}).budget ?? '')}${n.autoSign === false ? ' · 手动亲签' : ''}</span></label>`).join('')
    : '<div class="empty">没有种子需求（seed 后 data/dispatch.json 才有）</div>';
  const assts = [];
  USERS.forEach(u => (u.agents || []).forEach(a => { if (a.role === 'assistant') assts.push(a); }));
  $('dpAsst').innerHTML = assts.length ? assts.map(a => `<option value="${esc(a.id)}">${esc(Names.of(a.id))} · ${esc(a.id)}</option>`).join('') : '<option value="">没有助手</option>';
}
$('btnSeedNeedsRefresh').onclick = loadDispatch;
$('btnSeed').onclick = async () => {
  $('btnSeed').disabled = true; toast('正在注入种子，可能需要一会儿…');
  try { const r = await api('/api/admin/seed', {}); toast(`种子完成：服务 ${r.services} · 助手 ${r.assistants} · 分类树 ${r.taxonomy}`, 'ok'); await loadNames(); loadDispatch(); }
  catch (e) { toast(e.message, 'bad'); } finally { $('btnSeed').disabled = false; }
};
$('btnDispatch').onclick = async () => {
  const picked = [...$('seedNeeds').querySelectorAll('input:checked')].map(i => SEED_NEEDS[+i.dataset.i]);
  if (!picked.length) return toast('先选几条', 'bad');
  $('btnDispatch').disabled = true;
  try {
    const r = await api('/api/admin/dispatch', { gap: 2, needs: picked.map(n => ({ assistant_id: n.assistant_id, text: n.text, scope: n.scope, autoSign: n.autoSign, materials: n.materials })) });
    toast(`已投放 ${r.needs.length} 条`, 'ok');
  } catch (e) { toast(e.message, 'bad'); } finally { $('btnDispatch').disabled = false; }
};
$('dpAuto').onclick = () => { const s = $('dpAuto'); s.classList.toggle('on'); s.lastChild.textContent = s.classList.contains('on') ? '开' : '关'; };
$('btnDispatchOne').onclick = async () => {
  const aid = $('dpAsst').value, text = $('dpText').value.trim();
  if (!aid || !text) return toast('选助手、写需求', 'bad');
  try {
    const r = await api('/api/admin/dispatch', { gap: 0, needs: [{ assistant_id: aid, text, scope: { budget: Number($('dpBudget').value) || 30, category: $('dpCat').value.trim() || '不限' }, autoSign: $('dpAuto').classList.contains('on') }] });
    toast(`已投放：${r.needs[0]}`, 'ok'); $('dpText').value = '';
  } catch (e) { toast(e.message, 'bad'); }
};

/* ── 需求列表 ─────────────────────────────────────────────────── */
const stateClass = s => /completed|started/.test(s) ? 'ok' : /failed|aborted|lapsed|declined/.test(s) ? 'bad' : /arbitrated/.test(s) ? 'warn' : 'info';
async function loadNeeds() {
  let rows;
  try { rows = await api(SRC ? SRC + '/needs.json' : '/api/admin/needs'); } catch (e) { return toast(e.message, 'bad'); }
  $('needsTable').innerHTML = rows.length ? `<table class="tbl"><tr><th>时间</th><th>需求</th><th>助手</th><th>状态</th><th>合同卡</th><th class="num">模型调用 / token</th><th>工具轨迹</th></tr>` +
    rows.map((n, i) => `<tr><td class="nowrap mute">${ymdhm(n.created)}</td><td class="ellipsis" style="max-width:480px" title="${esc(n.text)}">${esc(n.text)}</td><td>${Names.html(n.assistant_id)}</td>
      <td><span class="badge ${stateClass(n.state)}">${esc(STATE[n.state] || n.state)}</span></td><td>${n.card_digest ? `<a href="#" data-cd="${esc(n.card_digest)}" class="mono">${esc(shortCd(n.card_digest))}</a>` : ''}</td>
      <td class="num">${n.llm_calls || 0} / ${n.tokens || 0}</td><td><button class="btn sm" data-tr="${i}">${(n.trace || []).length} 步</button></td></tr>
      <tr class="hide" id="trace-${i}"><td colspan="7"><div class="trace">${(n.trace || []).map((t, k) => `${String(k + 1).padStart(2, '0')} ${esc(t.tool)}  ${esc(t.args)}\n   → ${esc(t.result)}`).join('\n') || '（无）'}${n.result ? '\n\n结论：' + esc(JSON.stringify(n.result)) : ''}</div></td></tr>`).join('') + '</table>'
    : '<div class="empty">还没有需求</div>';
  $('needsTable').querySelectorAll('[data-tr]').forEach(b => b.onclick = () => $('trace-' + b.dataset.tr).classList.toggle('hide'));
  $('needsTable').querySelectorAll('[data-cd]').forEach(a => a.onclick = e => { e.preventDefault(); openOrder(a.dataset.cd); });
}
$('btnNeedsRefresh').onclick = loadNeeds;

/* ── 订单与检索 ─────────────────────────────────────────────── */
async function loadOrders() {
  try { ORDERS = SRC ? Object.values(await api(SRC + '/orders.json')) : await api('/api/admin/orders'); } catch (e) { return toast(e.message, 'bad'); }
  $('ordersTable').innerHTML = ORDERS.length ? `<table class="tbl"><tr><th>合同卡</th><th>助手 → 服务方</th><th>需求</th><th>成约</th><th>阶段</th><th>状态</th><th class="num">总价</th><th>更新</th></tr>` +
    ORDERS.map(o => `<tr class="click" data-cd="${esc(o.card_digest)}"><td><code class="mono" style="color:var(--c-market)">${esc(shortCd(o.card_digest))}</code></td>
      <td>${Names.html(o.assistant_id)}<span class="arrow">→</span><span class="who provider"><i></i>${esc(o.provider_name || Names.of(o.provider_id))}</span></td>
      <td class="ellipsis" style="max-width:360px">${esc(o.need_text || '')}</td><td>${esc(FORMATION[o.formation] || o.formation)}</td><td>${esc(o.phase)}</td>
      <td><span class="badge ${stateClass(o.state)}">${esc(STATE[o.state] || o.state)}</span></td><td class="num" style="color:var(--c-pay)">${money(o.total)}</td><td class="nowrap mute">${ymdhm(o.updated)}</td></tr>`).join('') + '</table>'
    : '<div class="empty">还没有订单</div>';
  $('ordersTable').querySelectorAll('[data-cd]').forEach(tr => tr.onclick = () => openOrder(tr.dataset.cd));
}
$('btnOrdersRefresh').onclick = loadOrders;
$('btnSearch').onclick = async () => {
  const q = $('searchCd').value.trim().replace(/^sha256:/, '').toLowerCase();
  if (!q) return;
  if (!ORDERS.length) { try { ORDERS = await api('/api/admin/orders'); } catch (e) { /* 空 */ } }
  const hits = ORDERS.filter(o => o.card_digest.replace(/^sha256:/, '').toLowerCase().startsWith(q));
  if (!hits.length) { $('searchResult').innerHTML = '<div class="empty">没有匹配的合同卡</div>'; return; }
  $('searchResult').innerHTML = hits.map(o => `<div><a href="#" data-cd="${esc(o.card_digest)}" class="mono">${esc(o.card_digest)}</a> <span class="mute">${esc(o.phase)}</span></div>`).join('');
  $('searchResult').querySelectorAll('[data-cd]').forEach(a => a.onclick = e => { e.preventDefault(); openOrder(a.dataset.cd); });
  if (hits.length === 1) openOrder(hits[0].card_digest);
};
$('searchCd').onkeydown = e => { if (e.key === 'Enter') $('btnSearch').click(); };

$('omClose').onclick = () => $('orderModal').classList.remove('on');
$('orderModal').onclick = e => { if (e.target === $('orderModal')) $('orderModal').classList.remove('on'); };
let STATIC_ORDERS = null;
async function openOrder(cd) {
  let o;
  try {
    if (SRC) { if (!STATIC_ORDERS) STATIC_ORDERS = await api(SRC + '/orders.json'); o = STATIC_ORDERS[cd]; if (!o) throw new Error('订单不在导出里'); }
    else o = await api('/api/orders/' + encodeURIComponent(cd));
  } catch (e) { return toast(e.message, 'bad'); }
  $('omCd').textContent = cd;
  const card = o.card || {}, periods = (card.derived && card.derived.periods) || [];
  let h = `<div class="kv"><dt>助手 → 服务方</dt><dd>${Names.html(o.assistant_id)}<span class="arrow">→</span><span class="who provider"><i></i>${esc(o.provider_name || Names.of(o.provider_id))}</span> <code class="mute">${esc(o.provider_id)}</code></dd>
    <dt>委托方账号</dt><dd>${Names.html(o.owner_id)} · 需求 <code>${esc(o.need_id)}</code></dd><dt>成约方式</dt><dd>${esc(FORMATION[o.formation] || o.formation)}</dd>
    <dt>阶段</dt><dd>${esc(o.phase)} <span class="badge ${stateClass(o.state)}">${esc(STATE[o.state] || o.state)}</span></dd>
    <dt>总价</dt><dd><b class="mono" style="color:var(--c-pay);font-size:20px">${money(o.total)}</b> 虚拟币${card.derived ? ` · 定金 ${money(card.derived.deposit)}` : ''}</dd>`;
  if (periods.length) h += `<dt>分期</dt><dd>${periods.map((p, i) => `第 ${i + 1} 期 ${money(p.amount)}${p.dueBy ? ` · 交付截止 ${esc(p.dueBy)}` : ''}${p.acceptanceWindow ? ` · 验收窗口 ${esc(p.acceptanceWindow)}` : ''}`).join('；')}</dd>`;
  if (card.cardId) h += `<dt>卡号</dt><dd><code>${esc(card.cardId)}</code> · 模板 <code class="mute">${esc(o.template_digest)}</code></dd>`;
  if (card.acceptance) h += `<dt>验收器</dt><dd>${Object.entries(card.acceptance).map(([k, v]) => `${esc(k)}：${Object.entries(v.verifiers || {}).map(([kind, ref]) => `${esc(kind)} <code>${esc(ref.id || '')}</code>`).join('，')}`).join('；')}</dd>`;
  h += `<dt>签名链</dt><dd>${sigChainHtml(o.signatures, { periods: periods.length || 1, formation: o.formation })}</dd>`;
  if (o.ruling) h += `<dt>裁定文书</dt><dd><span class="badge warn">${esc(OUTCOME[o.ruling.liable] || o.ruling.liable)}</span> · ${esc(OUTCOME[o.ruling.rulingKind] || o.ruling.rulingKind || '')}类 · 第 ${esc(o.ruling.period)} 期<div class="trace" style="margin-top:6px">${esc(JSON.stringify(o.ruling, null, 1))}</div></dd>`;
  h += '</div>';
  h += `<h3 style="margin:18px 0 6px;font-size:17px">事件时间线</h3><div class="timeline">` +
    (o.events || []).map(ev => { normEvent(ev); return `<div class="ev"><span class="t">${hms(ev.at)}</span><span>${esc(labelOf(ev))}${ev.period ? ` <span class="mute">第 ${ev.period} 期</span>` : ''}</span>
      <span>${Names.html(ev.sender)}<span class="arrow">→</span>${Names.html(ev.receiver)}</span><span class="${outcomeClass(ev.outcome)}">${ev.amount ? `<span class="mono" style="color:var(--c-pay)">${money(ev.amount)}</span> ` : ''}${esc(outcomeOf(ev))}</span></div>`; }).join('') + '</div>';
  $('omBody').innerHTML = h; $('orderModal').classList.add('on');
}

/* ── 回放列表 ─────────────────────────────────────────────────── */
async function loadRuns() {
  let runs;
  try { runs = await api('/api/admin/runs'); } catch (e) { return toast(e.message, 'bad'); }
  $('runsList').innerHTML = runs.length ? `<table class="tbl"><tr><th>运行</th><th>回放</th></tr>` + runs.reverse().map(r =>
    `<tr><td class="mono">${esc(r)}</td><td><a href="/admin?replay=${encodeURIComponent(r)}">交易流（按卡聚合）</a> · <a href="/admin?replay=${encodeURIComponent(r)}&tab=lanes">泳道</a> · <a href="/admin?replay=${encodeURIComponent(r)}&auto=1">auto</a></td></tr>`).join('') + '</table>'
    : '<div class="empty">还没有运行记录</div>';
}
$('btnNewRun').onclick = async () => { try { const r = await api('/api/admin/run', {}); toast('新一轮：' + r.run, 'ok'); loadRuns(); } catch (e) { toast(e.message, 'bad'); } };

/* ── 启动 ──────────────────────────────────────────────────── */
(async () => {
  let role = null;
  if (SCRIPT) { await enter(); return; }          // 剧本模式：静态数据，不需要会话
  try { role = (await api('/api/session')).role; } catch (e) { /* 服务未就绪 */ }
  if (role === 'admin') await enter();
  else if (AUTO) { $('loginView').classList.add('hide'); $('deniedView').classList.remove('hide'); }
})();
