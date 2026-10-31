/**
 * 用户端与管理员端共用：API 调用、中文标签、参与方名字解析、签名链渲染、事件 → 合同卡状态的归并。
 * 事件只是报文的影子（events.py）；这里只解释，不改任何协议状态。
 */
'use strict';
const $ = id => document.getElementById(id);
const el = (t, c, html) => { const n = document.createElement(t); if (c) n.className = c; if (html !== undefined) n.innerHTML = html; return n; };
const esc = s => String(s ?? '').replace(/[&<>"]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));

async function api(path, body, method) {
  const opt = { method: method || (body === undefined ? 'GET' : 'POST'), credentials: 'same-origin', headers: {} };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const err = new Error(data?.error?.message||`HTTP ${r.status}`); err.status = r.status; throw err; }
  return data;
}

let toastTimer = 0;
function toast(msg, kind) {
  let t = $('toast'); if (!t) { t = el('div'); t.id = 'toast'; document.body.appendChild(t); }
  t.className = 'toast on ' + (kind || ''); t.textContent = msg;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'toast'; }, 3200);
}

/* ── 时间与数字 ─────────────────────────────────────────────── */
const pad2 = n => String(n).padStart(2, '0');
function hms(at) { const d = new Date(at * 1000); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; }
function ymdhm(at) { if (!at) return '—'; const d = new Date(at * 1000); return `${d.getMonth() + 1}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }
function money(x) { if (x === null || x === undefined || x === '') return '—'; const n = Number(x); return Number.isFinite(n) ? n.toFixed(2) : String(x); }
function shortCd(cd) { if (!cd) return ''; const h = String(cd).split(':').pop(); return h.slice(0, 8); }
function dur(s) { if (s === null || s === undefined) return '—'; return s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${(s / 60).toFixed(1)} 分` : `${(s / 3600).toFixed(1)} 时`; }

/* ── 中文标签 ───────────────────────────────────────────────── */
// A2C / AP2 报文（与 events.py 的 LABELS 一致；/api/admin/events 的行没有 label 字段，这里兜底）
const OP_LABELS = {
  'interface.updated':'消息接口更新','message.queued':'消息已投递','message.processing':'消息处理中','message.completed':'消息已处理','message.rejected':'消息被拒绝','message.audit':'管理员消息审计',
  'template.get': '取合同模板', 'template.endorse': '市场签模板', 'request.submit': '提交提案卡',
  'card.endorse': '市场签3', 'card.countersign': '请求复签（签2）', 'work.start': '开工请求',
  'step.authorize': '环节授权', 'delivery.anchor': '交付留痕（签5）', 'anchor.watch': '订阅存证',
  'delivery.fetch': '取件', 'acceptance.notify': '验收通知', 'payment.notify': '付款通知',
  'termination.quote': '解除报价', 'termination.notify': '解除通知', 'card.close': '提交结清',
  'provenance.endorse': '出处加签', 'ap2.checkout': 'AP2 结账', 'ap2.pay': 'AP2 付款（读卡过账）',
  // 平台事件
  'user.register': '注册账号', 'assistant.register': '注册个人助手', 'service.register': '注册专业服务', 'seed.services': '注入种子服务',
  'wallet.adjust': '钱包发放 / 扣减', 'transfer': '读卡过账', 'transfer.plain': '普通转账', 'restart': '重启中止',
  'need.create': '下需求', 'need.start': '助手开始处理', 'need.contracted': '需求已定约', 'need.failed': '需求未成',
  'need.retry': '模型连接失败 · 稍后重试',
  'discovery.search': '市场检索（A2X）', 'confirm.request': '上升用户确认', 'confirm.answer': '用户答复',
  'order.signed': '签1 采纳合同卡', 'order.declined': '服务方拒绝复签', 'request.returned': '提案卡被退回', 'order.started': '已开工', 'order.fetched': '取件签收',
  'order.verified': '验收器判定（签6）', 'order.accepted': '验收付款（签7）', 'order.arbitrated': '裁定', 'order.completed': '结清',
  'order.paid-late': '补付本期款', 'order.error': '处理出错', 'order.step-answered': '环节授权答复', 'arbitration.ruling': '裁定文书', 'order.terminated': '合同解除', 'traffic.level': '背景流量档位', 'llm.failover': '模型端点切换', 'service.online': '外部服务方上线',
};
function labelOf(ev) {
  const op = ev.op || '';
  if (ev.label && ev.label !== op) return ev.label;
  if (OP_LABELS[op]) return OP_LABELS[op];
  if (op.endsWith('.result')) { const b = op.slice(0, -7); return (OP_LABELS[b] || b) + ' · 回执'; }
  return op;
}
const OUTCOME = { ok: '成功', completed: '完成', error: '失败', failed: '失败', working: '处理中', submitted: '已提交', rejected: '拒绝',
  granted: '允许', refused: '拒绝', timeout: '超时', pass: '通过', fail: '不通过', provider: '责任在服务方', client: '责任在委托方',
  online: '在线', listed: '挂牌', virtual: '临时账号', isp: '内部服务方', isc: '内部用户', external: '外部用户', welcome: '注册赠送', login: '登录赠送', seed: '初始额度', grant: '发放 / 扣减', deposit: '定金', installment: '期款',
  proposalFee: '拟卡费', sign: '定约亲签', accept: '验收亲签', step: '环节授权', arbitrate: '是否仲裁', 'a2x-tree': 'A2X 分类树', vector: '向量退路',
  'input-review': '输入审查', recompute: '复算', deadline: '时限' };
function outcomeOf(ev) {
  const o = ev.outcome || '';
  if (!o) return '';
  if (ev.period && /^(deposit|installment)$/.test(ev.detail && ev.detail.purpose)) return OUTCOME[o] || o;
  return OUTCOME[o] || o;
}
function outcomeClass(o) { return /^(ok|completed|granted|pass|online)$/.test(o) ? 'ok' : /^(error|failed|refused|timeout|fail|rejected)$/.test(o) ? 'bad' : ''; }
const STATE = { queued: '排队', open: '待 swarm 驾驭', running: '助手处理中', started: '已开工', forming: '定约中', completed: '已完成', failed: '未成', abandoned: '已放弃', terminated: '已解除',
  arbitrated: '已裁定', lapsed: '已失效', aborted: '已中止', declined: '被拒绝', pending: '待答复', granted: '已允许', refused: '已拒绝', timeout: '已超时' };
const FORMATION = { preapproval: '预批准', countersign: '待批准', proposal: '提案型' };
const KIND_LOG = { welcome: '注册赠送', login: '登录赠送', seed: '初始额度', grant: '发放 / 扣减', deposit: '定金', installment: '期款', proposalFee: '拟卡费', ruling: '裁定后付款', refund: '退差额', fee: '市场服务费' };

/* ── 参与方名字 ─────────────────────────────────────────────── */
const Names = {
  map: {},            // id → 名字
  kind: {},           // id → 颜色类
  ownerAsst: {},      // 用户 id → 助手 id
  owner: {},          // 任何智能体 id → 账号 id（聚焦按账号做）
  seedUsers(users) {
    const seen = {};
    users.forEach(u => {
      const nm = u.display_name || u.username || u.note || u.id;
      this.map[u.id] = nm; this.kind[u.id] = 'user';
      (u.agents || []).forEach(a => {
        this.owner[a.id] = u.id;
        if (a.role === 'assistant') {
          let n = a.name || a.id;
          if (seen[n]) { n = `${a.name}（${u.display_name || u.username || ''}）`; }
          seen[a.name] = true;
          this.map[a.id] = n; this.kind[a.id] = 'agent'; this.ownerAsst[u.id] = a.id;
        } else { this.map[a.id] = a.name || a.id; this.kind[a.id] = 'provider'; }
      });
    });
  },
  urlToId: {},        // 外部服务方 URL → 智能体 id
  seedServices(services) { services.forEach(s => { this.map[s.id] = s.name; this.kind[s.id] = 'provider'; if (s.owner_id) this.owner[s.id] = s.owner_id; if (s.external && s.url) this.urlToId[s.url] = s.id; }); },
  accountOf(id) { const k = this.key(id); if (!k) return ''; if (k.startsWith('u-')) return k; return this.owner[k] || ''; },
  seedMine(me) {
    this.map[me.id] = '我'; this.kind[me.id] = 'user';
    (me.agents || []).forEach(a => { this.map[a.id] = a.name; this.kind[a.id] = a.role === 'assistant' ? 'agent' : 'provider'; });
  },
  key(id) {
    if (!id) return '';
    if (this.urlToId[id]) return this.urlToId[id];
    let m;
    if ((m = /\/a2a\/providers\/([^/?#]+)/.exec(id))) return m[1];
    if (/\/a2a\/market/.test(id)) return 'market';
    if (/\/a2a\/pay/.test(id)) return 'pay';
    if ((m = /^urn:ioalab:(?:client|user):(.+)$/.exec(id))) return m[1];
    return id;
  },
  of(id) {
    const k = this.key(id);
    if (!k) return '—';
    if (k === 'market') return '市场'; if (k === 'pay') return '支付方';
    if (k === 'platform') return '平台'; if (k === 'admin') return '管理员';
    return this.map[k] || k;
  },
  kindOf(id) {
    const k = this.key(id);
    if (k === 'market') return 'market'; if (k === 'pay') return 'pay'; if (k === 'platform' || k === 'admin') return 'platform';
    return this.kind[k] || (k.startsWith('asst-') ? 'agent' : k.startsWith('svc-') ? 'provider' : k.startsWith('u-') ? 'user' : 'platform');
  },
  html(id) { if (!id) return '<span class="mute">—</span>'; const a = this.accountOf(id); return `<span class="who ${this.kindOf(id)}"${a ? ` data-acct="${esc(a)}"` : ''}><i></i>${esc(this.of(id))}</span>`; },
};

/* ── 聚焦：点任一助手 / 服务方 → 按账号高亮，其余变淡 ─────────── */
const Focus = {
  acct: null, listeners: [],
  set(acct) {
    this.acct = acct && acct !== this.acct ? acct : null;
    document.body.classList.toggle('focus-on', !!this.acct);
    this.refresh();
    this.listeners.forEach(f => { try { f(this.acct); } catch (e) { console.error(e); } });
  },
  clear() { if (this.acct) this.set(null); },
  /** 一条事件涉及的账号：发起方 / 接收方的账号 + 所属卡的助手与服务方的账号 */
  accountsOf(ev, c) {
    const out = new Set();
    [ev.sender, ev.receiver].forEach(id => { const a = Names.accountOf(id); if (a) out.add(a); });
    const cs = c || (ev.cardDigest && typeof cases !== 'undefined' ? cases.byCard[ev.cardDigest] : null);
    if (cs) { [cs.assistant, cs.provider].forEach(id => { const a = Names.accountOf(id); if (a) out.add(a); }); }
    if (ev.detail && ev.detail.provider) { const a = Names.accountOf(ev.detail.provider); if (a) out.add(a); }
    return [...out];
  },
  accountsOfCase(c) { return [...new Set([Names.accountOf(c.assistant), Names.accountOf(c.provider)].filter(Boolean))]; },
  mark(el, accts) {
    if (!el) return;
    el.dataset.accts = (accts || []).join(' ');
    if (this.acct) el.classList.toggle('hit', (accts || []).includes(this.acct));
  },
  refresh() {
    document.querySelectorAll('[data-accts]').forEach(el => {
      if (!this.acct) el.classList.remove('hit');
      else el.classList.toggle('hit', el.dataset.accts.split(' ').includes(this.acct));
    });
  },
};
document.addEventListener('click', e => {
  const t = e.target.closest('[data-acct]');
  if (t && t.dataset.acct) { e.stopPropagation(); Focus.set(t.dataset.acct); }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') Focus.clear(); });
const WHO_COLOR = { agent: 'var(--c-agent)', user: 'var(--c-user)', market: 'var(--c-market)', pay: 'var(--c-pay)', provider: 'var(--c-provider)', platform: 'var(--c-ext)' };

/* ── 签名链 ─────────────────────────────────────────────────── */
// 签1 委托方（或用户亲签）· 签2 服务方复签 · 签3 市场 · 签4(i) 服务方交付 · 签5(i) 市场时间戳 · 签6(i) 验收报告 · 签7(i) 验收（或用户亲签）
const SIG_WHO = { 1: 'agent', 2: 'provider', 3: 'market', 4: 'provider', 5: 'market', 6: 'agent', 7: 'agent' };
function emptySigs() { return { 1: false, 2: false, 3: false, 4: [], 5: [], 6: [], 7: [] }; }
function sigChainHtml(sigs, opts) {
  opts = opts || {};
  const s = sigs || emptySigs();
  const n = Math.max(opts.periods || 0, ...[4, 5, 6, 7].map(k => (s[k] || []).length), 1);
  const one = (label, on, who, sub) => `<span class="sig ${on ? 'on' : 'off'}" style="--who:${WHO_COLOR[who]}"><i></i>${label}${sub ? `<span class="p">${sub}</span>` : ''}</span>`;
  let h = one('签1', !!s[1], SIG_WHO[1]);
  if (opts.formation !== 'preapproval' || s[2]) h += one('签2', !!s[2], SIG_WHO[2]);
  h += one('签3', !!s[3], SIG_WHO[3]);
  for (let i = 1; i <= n; i++) {
    h += '<span class="sep"></span>';
    [4, 5, 6, 7].forEach(k => { h += one(`签${k}(${i})`, !!(s[k] || [])[i - 1], SIG_WHO[k]); });
  }
  return `<div class="sigs">${h}</div>`;
}

/* ── 事件 → 合同卡（泳道与按卡聚合共用的归并器）──────────────── */
const PHASES = ['发现', '定约', '开工', '交付', '验收', '结清'];
const PHASE_OF = {
  'need.create': 0, 'need.start': 0, 'discovery.search': 0, 'template.get': 0, 'template.get.result': 0,
  'order.signed': 1, 'card.endorse': 1, 'card.endorse.result': 1, 'card.countersign': 1, 'card.countersign.result': 1, 'request.submit': 1,
  'request.submit.result': 1, 'ap2.checkout': 1, 'order.declined': 1, 'request.returned': 1, 'need.contracted': 1,
  'work.start': 2, 'work.start.result': 2, 'order.started': 2, 'anchor.watch': 2, 'step.authorize': 2, 'order.step-answered': 2,
  'delivery.anchor': 3, 'delivery.anchor.result': 3, 'delivery.fetch': 3, 'delivery.fetch.result': 3, 'order.fetched': 3,
  'order.verified': 4, 'acceptance.notify': 4, 'acceptance.notify.result': 4, 'order.accepted': 4, 'payment.notify': 4, 'payment.notify.result': 4,
  'order.paid-late': 4, 'order.arbitrated': 4, 'arbitration.ruling': 4,
  'card.close': 5, 'card.close.result': 5, 'order.completed': 5,
};
function normEvent(ev) {       // /api/admin/events 的行用 card_digest / run_id；WebSocket 与回放用 cardDigest / run
  if (ev.cardDigest === undefined && ev.card_digest !== undefined) ev.cardDigest = ev.card_digest;
  if (typeof ev.detail === 'string') { try { ev.detail = JSON.parse(ev.detail); } catch (e) { ev.detail = {}; } }
  ev.detail = ev.detail || {};
  return ev;
}
class Cases {
  constructor() { this.list = []; this.byCard = {}; this.pendingByAsst = {}; this.platform = null; }
  _new(asst) {
    const c = { key: 'c' + (this.list.length + 1), assistant: asst || '', provider: '', cd: null, sigs: emptySigs(), phase: -1, state: '进行中',
      stateClass: 'info', events: [], lastAt: 0, firstAt: 0, total: null, paid: 0, periods: 0, periodMax: 0, el: null, laneEl: null };
    this.list.push(c);
    return c;
  }
  _platform() { if (!this.platform) { this.platform = this._new(''); this.platform.key = 'platform'; this.platform.isPlatform = true; this.platform.state = ''; } return this.platform; }
  assistantOf(ev) {
    const s = Names.key(ev.sender), r = Names.key(ev.receiver);
    if (ev.detail.assistant) return ev.detail.assistant;
    if (Names.kindOf(s)==='agent') return s;
    if (Names.kindOf(r)==='agent') return r;
    if (ev.op === 'confirm.request' && Names.ownerAsst[r]) return Names.ownerAsst[r];
    if (ev.op === 'confirm.answer' && Names.ownerAsst[s]) return Names.ownerAsst[s];
    return '';
  }
  providerOf(ev) {
    if (ev.detail && ev.detail.provider) return ev.detail.provider;
    for (const id of [ev.receiver, ev.sender]) if (/\/a2a\/providers\//.test(id || '')) return id;
    return '';
  }
  /** 把一条事件归到某张卡，返回 {case, isNew, changed} */
  ingest(ev) {
    normEvent(ev);
    const cd = ev.cardDigest || null;
    const asst = this.assistantOf(ev);
    let c = null, isNew = false;
    if (cd && this.byCard[cd]) c = this.byCard[cd];
    else if (cd) {
      c = (asst && this.pendingByAsst[asst]) || null;
      if (!c && !asst && ev.op === 'transfer') c = null;
      if (!c) { c = this._new(asst); isNew = true; }
      c.cd = cd; this.byCard[cd] = c;
      if (asst && this.pendingByAsst[asst] === c) delete this.pendingByAsst[asst];
    } else if (asst) {
      if (ev.op === 'need.create' || ev.op === 'need.start') {
        c = this.pendingByAsst[asst];
        if (!c || c.cd || c.events.some(e => e.op === 'need.start')) { c = this._new(asst); isNew = true; this.pendingByAsst[asst] = c; }
      } else c = this.pendingByAsst[asst] || null;
      if (!c) { c = this._platform(); isNew = !c.events.length; }
    } else if (ev.op === 'discovery.search') {
      const pend = Object.values(this.pendingByAsst).filter(x => !x.cd).sort((a, b) => b.lastAt - a.lastAt)[0];
      c = pend || this._platform(); isNew = c.isPlatform && !c.events.length;
    } else { c = this._platform(); isNew = !c.events.length; }
    if (!c.assistant && asst) c.assistant = asst;
    const pv = this.providerOf(ev); if (pv && (!c.provider || ev.op === 'order.signed')) c.provider = pv;
    c.events.push(ev); c.lastAt = ev.at || c.lastAt; if (!c.firstAt) c.firstAt = ev.at || 0;
    this._apply(c, ev);
    return { c, isNew };
  }
  _apply(c, ev) {
    const op = ev.op, p = ev.period, ok = !/^(error|failed)$/.test(ev.outcome || '');
    if (PHASE_OF[op] !== undefined && ok) c.phase = Math.max(c.phase, PHASE_OF[op]);
    const setP = (k, i) => { if (!i) return; const a = c.sigs[k]; while (a.length < i) a.push(false); a[i - 1] = true; c.periodMax = Math.max(c.periodMax, i); };
    if (op === 'order.signed') c.sigs[1] = true;
    if (op === 'card.countersign' && ok) c.sigs[2] = true;
    if (op === 'card.endorse' && ok) c.sigs[3] = true;
    if (op === 'delivery.anchor' && ok) { setP(4, p); setP(5, p); }
    if (op === 'order.verified' || (op === 'acceptance.notify' && ok)) setP(6, p);
    if (op === 'order.accepted' || op === 'order.paid-late') setP(7, p);
    if (op === 'ap2.pay' && ok && ev.amount && (ev.detail.purpose === 'installment')) setP(7, p);
    if (op === 'transfer' && ev.amount) c.paid += Number(ev.amount) || 0;
    if (op === 'order.declined') { c.sigs[2] = false; c.state = '被拒绝'; c.stateClass = 'bad'; }
    if (op === 'order.started') { c.state = '进行中'; c.stateClass = 'info'; }
    if (op === 'order.completed' || op === 'card.close') { c.state = '已结清'; c.stateClass = 'ok'; }
    if (op === 'order.arbitrated' || op === 'arbitration.ruling') { c.state = '已裁定 · ' + (OUTCOME[ev.outcome] || ev.outcome || ''); c.stateClass = 'warn'; }
    if (op === 'need.failed') { c.state = '未成'; c.stateClass = 'bad'; }
    if (op === 'order.error') { c.state = '出错'; c.stateClass = 'bad'; }
    if (ev.detail.evidenceTrust === 'verified' && ev.detail.signatures) {
      c.sigs=ev.detail.signatures;c.total=ev.detail.total;c.periods=ev.detail.periods;
      c.state=({completed:'已结清',arbitrated:'已裁定', 'client-terminated':'已解除',started:'进行中',forming:'签约中'})[ev.detail.state] || '状态未知';
      c.stateClass=ev.detail.state==='completed'?'ok':ev.detail.state==='arbitrated'?'warn':'info';
    }
  }
}

/* ── 虚拟时钟（动效全部按它算，录制工具用假时钟逐帧推进）────── */
const Clock = {
  virtual: false, playing: false, t0: 0, paused: 0,
  now() { return this.virtual ? (this.playing ? performance.now() - this.t0 : this.paused) : performance.now(); },
  play() { this.playing = true; this.t0 = performance.now() - this.paused; },
  pause() { this.paused = this.now(); this.playing = false; },
};
/** 入场动画：元素 data-born 记虚拟时刻，每帧按经过时间算透明度与位移 */
const Born = { list: [], add(node, dur) { node.dataset.born = Clock.now(); node._dur = dur || 380; node.style.opacity = 0; this.list.push(node); } };
function animateBorn(n) {
  for (let i = Born.list.length - 1; i >= 0; i--) {
    const node = Born.list[i], p = Math.min(Math.max((n - +node.dataset.born) / node._dur, 0), 1), k = 1 - Math.pow(1 - p, 3);
    node.style.opacity = k; node.style.transform = `translateY(${8 * (1 - k)}px)`;
    if (p >= 1) { node.style.transform = ''; Born.list.splice(i, 1); }
  }
}
