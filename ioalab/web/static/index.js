/**
 * 用户端：注册 / 登录 → 身份（助手 · 专业服务）→ 钱包 → 下需求 → 需求与订单 → 订单详情（签名链）→ 待确认事项。
 * 每 3 秒轮询 /api/me 与 /api/needs（订单列表顺带刷新）。
 */
'use strict';
let CONFIG = { mode: 'dev', fixedCode: true };
let ME = null, DEFS = null, authMode = 'register';
let pollTimer = 0;

/* ── 注册 / 登录 ───────────────────────────────────────────── */
function setAuthMode(m) {
  authMode = m;
  document.querySelectorAll('[data-auth]').forEach(b => b.classList.toggle('on', b.dataset.auth === m));
  const open = !CONFIG.fixedCode;
  $('rowCode').classList.toggle('hide', open); $('lbCode').classList.toggle('hide', open);
  $('password').classList.toggle('hide', !open); $('lbPw').classList.toggle('hide', !open);
  const inv = open && m === 'register';
  $('invite').classList.toggle('hide', !inv); $('lbInvite').classList.toggle('hide', !inv);
  $('btnAuth').textContent = m === 'register' ? '注册并登录' : '登录';
  $('authHint').textContent = open ? 'open 模式：密码登录，注册需要管理员生成的邀请码。' : 'dev 模式：验证码固定为 123456，点「获取验证码」后直接显示在页面上。';
}
document.querySelectorAll('[data-auth]').forEach(b => b.onclick = () => setAuthMode(b.dataset.auth));
$('btnCode').onclick = async () => {
  try { const r = await api('/api/auth/code', { phone: $('phone').value.trim() }); $('codeShow').textContent = r.code || ''; $('code').value = r.code || ''; }
  catch (e) { toast(e.message, 'bad'); }
};
$('btnAuth').onclick = async () => {
  const body = { phone: $('phone').value.trim(), code: $('code').value.trim(), password: $('password').value, invite: $('invite').value.trim() };
  try { await api(authMode === 'register' ? '/api/auth/register' : '/api/auth/login', body); await enter(); }
  catch (e) { toast(e.message, 'bad'); }
};
$('btnLogout').onclick = async () => { await api('/api/auth/logout', {}); location.reload(); };

/* ── 主界面 ─────────────────────────────────────────────────── */
async function enter() {
  $('authView').classList.add('hide'); $('mainView').classList.remove('hide'); $('btnLogout').classList.remove('hide');
  await refresh(); await loadWallet();
  clearInterval(pollTimer); pollTimer = setInterval(refresh, 3000);
}
async function refresh() {
  try {
    const [me, needs, orders] = await Promise.all([api('/api/me'), api('/api/needs'), api('/api/orders')]);
    ME = me; Names.seedMine(me);
    $('whoPhone').textContent = me.phone; $('walletTop').textContent = money(me.wallet); $('walletBal').textContent = money(me.wallet);
    renderIdentity(me); renderPending(me.pending || []); renderNeeds(needs); renderOrders(orders);
  } catch (e) {
    if (e.status === 401) { clearInterval(pollTimer); location.reload(); } else toast(e.message, 'bad');
  }
}

function renderIdentity(me) {
  const asst = (me.agents || []).find(a => a.role === 'assistant');
  const svcs = (me.agents || []).filter(a => a.role === 'service');
  let h = '<div class="kv">';
  h += `<dt>个人助手</dt><dd>${asst ? `<span class="who agent"><i></i>${esc(asst.name)}</span> <span class="badge ok">${esc(OUTCOME[asst.status] || asst.status)}</span> <code class="mute">${esc(asst.id)}</code>`
    : `<div class="row" style="display:flex;gap:10px;align-items:center"><input id="asstName" value="小艺" style="max-width:160px"><button class="btn sm primary" id="btnAsst">注册个人助手（送 100 虚拟币）</button></div>`}</dd>`;
  h += `<dt>专业服务</dt><dd>${svcs.length ? svcs.map(s => `<div><span class="who provider"><i></i>${esc(s.name)}</span> <span class="badge ${s.status === 'online' ? 'ok' : ''}">${esc(OUTCOME[s.status] || s.status)}</span> <code class="mute">${esc(s.id)}</code></div>`).join('') : '<span class="mute">尚未注册</span>'}`;
  if (svcs.length < 3) h += `<div class="row" style="display:flex;gap:10px;align-items:center;margin-top:8px"><select id="svcDef" style="max-width:320px"><option value="">加载服务定义…</option></select><input id="svcName" placeholder="服务名（可留空）" style="max-width:200px"><button class="btn sm" id="btnSvc">注册专业服务</button></div>`;
  h += '</dd></div><p>助手是委托方（花虚拟币），专业服务是服务方（挣虚拟币）；每账号最多 3 个服务。</p>';
  $('identity').innerHTML = h;
  if ($('btnAsst')) $('btnAsst').onclick = async () => {
    try { await api('/api/assistant', { name: $('asstName').value.trim() || '小艺' }); toast('已注册个人助手，赠送 100 虚拟币', 'ok'); await refresh(); await loadWallet(); }
    catch (e) { toast(e.message, 'bad'); }
  };
  if ($('svcDef')) fillDefs();
  if ($('btnSvc')) $('btnSvc').onclick = async () => {
    const id = $('svcDef').value; if (!id) return toast('先选一个服务定义', 'bad');
    try { await api('/api/service', { definition_id: id, name: $('svcName').value.trim() }); toast('已注册专业服务，模板已由市场签认', 'ok'); await refresh(); }
    catch (e) { toast(e.message, 'bad'); }
  };
}
async function fillDefs() {
  try { DEFS = DEFS || await api('/api/services/definitions'); } catch (e) { return; }
  const s = $('svcDef'); if (!s) return;
  s.innerHTML = '<option value="">选择服务定义</option>' + DEFS.map(d => `<option value="${esc(d.id)}">${esc(d.name)} · ${FORMATION[d.formation] || d.formation}${d.price ? ' · ' + esc(d.price) : ''}</option>`).join('');
}

function renderPending(list) {
  $('pendingBox').classList.toggle('hide', !list.length);
  $('pendingList').innerHTML = list.map(p => `<div class="it"><span class="k">${esc(OUTCOME[p.kind] || p.kind)}</span><span class="txt">${esc(p.text)}</span>
    <span class="mute nowrap">${hms(p.created)}</span><button class="btn sm ok" data-cf="${esc(p.id)}" data-g="1">本次允许</button><button class="btn sm bad" data-cf="${esc(p.id)}" data-g="0">拒绝</button></div>`).join('');
  $('pendingList').querySelectorAll('[data-cf]').forEach(b => b.onclick = async () => {
    try { await api('/api/confirmations/' + b.dataset.cf, { granted: b.dataset.g === '1' }); toast(b.dataset.g === '1' ? '已允许' : '已拒绝'); await refresh(); }
    catch (e) { toast(e.message, 'bad'); }
  });
}

function renderNeeds(rows) {
  if (!rows.length) { $('needList').innerHTML = '<div class="empty">还没有需求</div>'; return; }
  $('needList').innerHTML = `<table class="tbl"><tr><th>时间</th><th>需求</th><th>状态</th><th>合同卡</th><th class="num">模型调用 / token</th></tr>` +
    rows.map(n => `<tr class="click" data-nid="${esc(n.id)}"><td class="nowrap mute">${ymdhm(n.created)}</td><td class="ellipsis" style="max-width:520px">${esc(n.text)}</td>
      <td><span class="badge ${stateClass(n.state)}">${esc(STATE[n.state] || n.state)}</span></td><td><code class="mono" style="color:var(--c-market)">${esc(shortCd(n.card_digest))}</code></td>
      <td class="num">${n.llm_calls || 0} / ${n.tokens || 0}</td></tr>`).join('') + '</table>';
  $('needList').querySelectorAll('[data-nid]').forEach(tr => tr.onclick = () => openNeed(tr.dataset.nid));
}
function stateClass(s) { return /completed|started/.test(s) ? 'ok' : /failed|aborted|lapsed|declined/.test(s) ? 'bad' : /arbitrated/.test(s) ? 'warn' : 'info'; }
function renderOrders(rows) {
  if (!rows.length) { $('orderList').innerHTML = '<div class="empty">还没有订单</div>'; return; }
  $('orderList').innerHTML = `<table class="tbl"><tr><th>合同卡</th><th>成约</th><th>阶段</th><th>状态</th><th class="num">总价</th><th>更新</th></tr>` +
    rows.map(o => `<tr class="click" data-cd="${esc(o.card_digest)}"><td><code class="mono" style="color:var(--c-market)">${esc(shortCd(o.card_digest))}</code></td>
      <td>${esc(FORMATION[o.formation] || o.formation)}</td><td>${esc(o.phase)}</td><td><span class="badge ${stateClass(o.state)}">${esc(STATE[o.state] || o.state)}</span></td>
      <td class="num" style="color:var(--c-pay)">${money(o.total)}</td><td class="nowrap mute">${ymdhm(o.updated)}</td></tr>`).join('') + '</table>';
  $('orderList').querySelectorAll('[data-cd]').forEach(tr => tr.onclick = () => openOrder(tr.dataset.cd));
}

async function loadWallet() {
  try {
    const w = await api('/api/wallet');
    $('walletBal').textContent = money(w.balance); $('walletTop').textContent = money(w.balance);
    $('walletLog').innerHTML = w.log.length ? `<table class="tbl"><tr><th>时间</th><th>类别</th><th class="num">变动</th><th class="num">余量</th><th>说明</th></tr>` +
      w.log.map(l => `<tr><td class="nowrap mute">${ymdhm(l.at)}</td><td>${esc(KIND_LOG[l.kind] || l.kind)}${l.ref ? ` <code class="mute">${esc(shortCd(l.ref))}</code>` : ''}</td>
        <td class="num ${Number(l.delta) >= 0 ? 'ok' : 'bad'}">${Number(l.delta) >= 0 ? '+' : ''}${money(l.delta)}</td><td class="num">${money(l.balance)}</td><td class="mute">${esc(l.note || '')}</td></tr>`).join('') + '</table>'
      : '<div class="empty">暂无流水</div>';
  } catch (e) { toast(e.message, 'bad'); }
}
$('btnWalletRefresh').onclick = loadWallet;

/* ── 下需求 ─────────────────────────────────────────────────── */
['swSign1', 'swSign7', 'swAuto'].forEach(id => $(id).onclick = () => { const s = $(id); s.classList.toggle('on'); if (id === 'swAuto') s.lastChild.textContent = s.classList.contains('on') ? '开' : '关'; });
$('btnNeed').onclick = async () => {
  const text = $('needText').value.trim(); if (!text) return toast('先写一句话需求', 'bad');
  const body = { text, scope: { budget: Number($('needBudget').value) || 30, category: $('needCategory').value.trim() || '不限' },
    sign1: $('swSign1').classList.contains('on'), sign7: $('swSign7').classList.contains('on'), autoSign: $('swAuto').classList.contains('on') };
  try { const n = await api('/api/needs', body); toast(`已交给助手：${n.id}`, 'ok'); $('needText').value = ''; await refresh(); }
  catch (e) { toast(e.message, 'bad'); }
};

/* ── 详情 ──────────────────────────────────────────────────── */
function closeModal(id) { $(id).classList.remove('on'); }
$('omClose').onclick = () => closeModal('orderModal'); $('nmClose').onclick = () => closeModal('needModal');
document.querySelectorAll('.modal').forEach(m => m.onclick = e => { if (e.target === m) m.classList.remove('on'); });

async function openOrder(cd) {
  let o;
  try { o = await api('/api/orders/' + encodeURIComponent(cd)); } catch (e) { return toast(e.message, 'bad'); }
  $('omCd').textContent = cd;
  const card = o.card || {}, periods = (card.derived && card.derived.periods) || [];
  const provider = o.provider_name || Names.of(o.provider_id);
  let h = `<div class="kv"><dt>服务方</dt><dd><span class="who provider"><i></i>${esc(provider)}</span> <code class="mute">${esc(o.provider_id)}</code></dd>
    <dt>助手</dt><dd>${Names.html(o.assistant_id)}</dd><dt>成约方式</dt><dd>${esc(FORMATION[o.formation] || o.formation)}</dd>
    <dt>阶段</dt><dd>${esc(o.phase)} <span class="badge ${stateClass(o.state)}">${esc(STATE[o.state] || o.state)}</span></dd>
    <dt>总价</dt><dd><b class="mono" style="color:var(--c-pay);font-size:20px">${money(o.total)}</b> 虚拟币${card.derived ? ` · 定金 ${money(card.derived.deposit)}` : ''}</dd>`;
  if (periods.length) h += `<dt>分期</dt><dd>${periods.map((p, i) => `第 ${i + 1} 期 ${money(p.amount)}${p.dueBy ? ` · 交付截止 ${esc(p.dueBy)}` : ''}`).join('；')}</dd>`;
  if (card.cardId) h += `<dt>卡号</dt><dd><code>${esc(card.cardId)}</code> · 模板 <code class="mute">${esc(shortCd(o.template_digest))}</code></dd>`;
  h += `<dt>签名链</dt><dd>${sigChainHtml(o.signatures, { periods: periods.length || 1, formation: o.formation })}</dd>`;
  if (o.ruling) h += `<dt>裁定文书</dt><dd><span class="badge warn">${esc(OUTCOME[o.ruling.liable] || o.ruling.liable)}</span> · ${esc(OUTCOME[o.ruling.rulingKind] || o.ruling.rulingKind || '')}类 · 第 ${esc(o.ruling.period)} 期</dd>`;
  h += '</div>';
  h += `<h3 style="margin:18px 0 6px;font-size:17px">事件时间线 <span class="mute" style="font-weight:400;font-size:14px">（报文的影子，不展示任务 id）</span></h3><div class="timeline">` +
    (o.events || []).map(ev => { normEvent(ev); return `<div class="ev"><span class="t">${hms(ev.at)}</span><span>${esc(labelOf(ev))}${ev.period ? ` <span class="mute">第 ${ev.period} 期</span>` : ''}</span>
      <span>${Names.html(ev.sender)}<span class="arrow">→</span>${Names.html(ev.receiver)}</span><span class="${outcomeClass(ev.outcome)}">${ev.amount ? `<span class="mono" style="color:var(--c-pay)">${money(ev.amount)}</span> ` : ''}${esc(outcomeOf(ev))}</span></div>`; }).join('') + '</div>';
  $('omBody').innerHTML = h; $('orderModal').classList.add('on');
}
async function openNeed(nid) {
  let n;
  try { n = await api('/api/needs/' + encodeURIComponent(nid)); } catch (e) { return toast(e.message, 'bad'); }
  $('nmId').textContent = nid;
  const sc = n.scope || {};
  let h = `<div class="kv"><dt>需求</dt><dd>${esc(n.text)}</dd><dt>状态</dt><dd><span class="badge ${stateClass(n.state)}">${esc(STATE[n.state] || n.state)}</span></dd>
    <dt>连接授权</dt><dd>类别 ${esc(sc.category)} · 预算上限 ${esc(sc.budget)} · 单笔上限 ${esc(sc.perTx)} · 有效期 ${esc(sc.validity)} · 定约亲签 ${sc.sign1 === 'user' ? '开' : '关'} · 验收亲签 ${sc.sign7 === 'user' ? '开' : '关'} · 仿真代签 ${n.auto_sign ? '开' : '关'}</dd>
    <dt>合同卡</dt><dd>${n.card_digest ? `<a href="#" data-cd="${esc(n.card_digest)}"><code>${esc(n.card_digest)}</code></a>` : '<span class="mute">尚未定约</span>'}</dd>
    <dt>模型用量</dt><dd>${n.llm_calls || 0} 次调用 · ${n.tokens || 0} token</dd>`;
  if (n.result) h += `<dt>助手结论</dt><dd><code class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(n.result, null, 1))}</code></dd>`;
  h += '</div>';
  if ((n.trace || []).length) h += `<h3 style="margin:18px 0 6px;font-size:17px">工具轨迹</h3><div class="trace">${n.trace.map((t, i) => `${String(i + 1).padStart(2, '0')} ${esc(t.tool)}  ${esc(t.args)}\n   → ${esc(t.result)}`).join('\n')}</div>`;
  $('nmBody').innerHTML = h; $('needModal').classList.add('on');
  $('nmBody').querySelectorAll('[data-cd]').forEach(a => a.onclick = e => { e.preventDefault(); closeModal('needModal'); openOrder(a.dataset.cd); });
}

/* ── 启动 ──────────────────────────────────────────────────── */
(async () => {
  try { CONFIG = await api('/api/config'); } catch (e) { /* 服务未就绪时沿用默认 */ }
  $('modeTag').textContent = `${CONFIG.mode} 模式 · ${CONFIG.llm ? '模型已配置' : '无模型（只能跑桩）'}`;
  setAuthMode('register');
  try { if ((await api('/api/session')).role === 'user') await enter(); } catch (e) { /* 未登录 */ }
})();
