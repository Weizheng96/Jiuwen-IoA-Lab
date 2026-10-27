/**
 * 全景：实时拓扑——左 账号与个人助手 · 中 交易中心（注册中心 · 合同模板公示 · 存证与签名 · 信誉日志与仲裁）与支付中心 · 右 服务方按领域成块。
 * 节点来自 /api/admin/users 与 /api/admin/registry，事件（WebSocket 或回放）画成节点状态变化与节点间脉冲。
 * 版面是普通 DOM 流式布局，SVG 覆盖层按元素实时坐标画线；动效按 Clock.now() 逐帧算（common.js），由 admin.js 的 loop 调 Panorama.tick。
 * 点任一助手 / 服务方 → Focus.set(账号)：同账号的节点与事件高亮，其余变淡（common.js Focus）。
 */
'use strict';
const Panorama = (() => {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const COLORS = { agent: '#38d3f0', provider: '#6aa9ff', market: '#b394fb', pay: '#fbbf24', user: '#4ade80', bad: '#f87171', ok: '#34d399' };
  const ease = p => 1 - Math.pow(1 - p, 3);
  const N = { asst: {}, svc: {}, byUser: {} };
  const Stat = { asst: 0, svc: 0, signed: 0, transfers: 0, volume: 0, completed: 0, arbitrated: 0, endorsed: 0, sig3: 0, sig5: 0, declined: 0, searches: 0 };
  const Anims = [], Timers = [], links = [];
  let baseSeq = 0, replay = false, ready = false, touched = [], pendingCredit = {}, quiet = false;   // quiet：历史事件只改状态不画脉冲
  let serverStats=null;
  const observedMessages=new Set(), anchoredCards=new Set();

  function anim(dur, fn, done) { Anims.push({ start: Clock.now(), dur, fn, done }); }
  function later(ms, fn) { Timers.push({ at: Clock.now() + ms, fn }); }
  function flash(el, cls, ms) { if (!el || quiet) return; el.classList.add(cls); later(ms || 900, () => el.classList.remove(cls)); }
  function fade(el, dur) { el.style.opacity = 0; anim(dur || 420, p => { el.style.opacity = ease(p); }); }
  function touch(el) { if (el) touched.push({ el, at: Clock.now() }); }
  const money = x => Number(x || 0).toFixed(2);

  function center(el) {
    const r = el.getBoundingClientRect(), m = $('pn').getBoundingClientRect();
    return { x: r.left + r.width / 2 - m.left, y: r.top + r.height / 2 - m.top };
  }
  function edge(el, side) {
    const r = el.getBoundingClientRect(), m = $('pn').getBoundingClientRect();
    return { x: (side === 'right' ? r.right : r.left) - m.left, y: r.top + r.height / 2 - m.top };
  }

  /* ── 节点 ─────────────────────────────────────────────────── */
  function kindTag(k) { return OUTCOME[k] || k || ''; }
  function mkAssistant(a, u) {
    if (N.asst[a.id]) return N.asst[a.id];
    const el = document.createElement('div'); el.className = 'pn-asst'; el.dataset.id = a.id; el.dataset.acct = u.id; el.dataset.accts = u.id;
    const experimental = (() => { try { return (typeof a.meta === 'string' ? JSON.parse(a.meta || '{}') : a.meta || {}).source==='experiment'; } catch (e) { return false; } })();
    el.innerHTML = `<div class="ava">${esc((u.display_name || u.username || '').slice(-2))}</div>
      <div class="name"><b>${esc(u.display_name || u.username)}</b><small>${esc(experimental?'独立SDK客户端':a.name || '个人助手')}</small></div>
      <div class="kind kind-${esc(u.kind)}">${esc(experimental?'仿真助手':kindTag(u.kind))}</div><div class="wallet">${money(u.balance)}</div>
      <div class="state">空闲</div><div class="sigs"><i class="s1"></i><i class="p s2"></i><i class="m s3"></i><i class="p s4"></i><i class="m s5"></i><i class="s6"></i><i class="s7"></i></div>`;
    $('pnAsstList').appendChild(el);
    const n = { el, id: a.id, uid: u.id, wallet: Number(u.balance || 0), provider: null, link: null, state: '空闲', st: el.querySelector('.state'), w: el.querySelector('.wallet') };
    N.asst[a.id] = n; N.byUser[u.id] = a.id; Stat.asst++;
    if (ready) fade(el, 500);
    if (pendingCredit[u.id]) { setWallet(n, n.wallet + pendingCredit[u.id]); delete pendingCredit[u.id]; }
    return n;
  }
  function mkBlock(cat) {
    let b = document.querySelector(`#pnBlocks .pn-block[data-cat="${cat.id}"]`);
    if (b) return b;
    b = document.createElement('div'); b.className = 'pn-block'; b.dataset.cat = cat.id;
    b.innerHTML = `<div class="bh"><b>${esc(cat.name)}</b><small>${esc(cat.owner || '')}</small></div><div class="bl"></div>`;
    $('pnBlocks').appendChild(b);
    return b;
  }
  function mkService(s, cats) {
    if (N.svc[s.id]) return N.svc[s.id];
    const cat = cats[s.category] || { id: s.category || 'cat_misc', name: '其他' };
    const b = mkBlock({ ...cat, owner: s.owner_id ? Names.of(s.owner_id) : '' });
    const el = document.createElement('div'); el.className = 'pn-svc'; el.dataset.id = s.id; el.dataset.acct = s.owner_id || ''; el.dataset.accts = s.owner_id || '';
    const form = FORMATION[s.formation] || s.formation || '';
    el.innerHTML = `<div class="name">${s.external ? '<span class="ext" title="外部服务方">⇄</span> ' : ''}${esc(s.name)}</div><div class="meta">${esc(form)} · <b>${s.price === null || s.price === undefined ? '报价' : esc(s.price)}</b><span class="rep">${s.reputation && s.reputation.completed ? ` · ${s.reputation.completed} 单` : ''}</span></div>
      <div class="st ${s.observation === 'connected' ? 'ok' : ''}">${s.observation === 'unknown' ? '观察中断 / 状态未知' : esc(OUTCOME[s.status] || s.status || '')}</div><div class="earn" title="本服务收入">${s.earned ? '+' + money(s.earned) : ''}</div>`;
    b.querySelector('.bl').appendChild(el);
    const n = { el, id: s.id, owner: s.owner_id, earned: 0, done: s.reputation ? s.reputation.completed || 0 : 0, okc: 0, st: el.querySelector('.st'), rep: el.querySelector('.rep'), earnEl: el.querySelector('.earn') };
    N.svc[s.id] = n; Stat.svc++;
    if (ready) fade(el, 420);
    return n;
  }
  function svcNode(id) { return N.svc[Names.key(id)] || null; }
  function nodeEl(ref) {
    if (!ref) return null;
    if (ref instanceof Element) return ref;
    const k = Names.key(ref);
    if (k === 'market') return $('pnMarket'); if (k === 'pay') return $('pnPay');
    if (N.asst[k]) return N.asst[k].el; if (N.svc[k]) return N.svc[k].el;
    if (N.byUser[k]) return N.asst[N.byUser[k]].el;
    return null;
  }
  function setState(n, text, cls) { if (!n) return; n.state = text; n.st.textContent = text; n.st.className = 'state ' + (cls || ''); touch(n.el); }
  function setSvcState(n, text, cls) { if (!n) return; n.st.textContent = text; n.st.className = 'st ' + (cls || ''); touch(n.el); }
  function sig(n, k, on) { if (!n) return; const i = n.el.querySelector('.s' + k); if (i) i.classList.toggle('on', on !== false); }
  function resetSigs(n) { if (!n) return; n.el.querySelectorAll('.sigs i').forEach(i => i.classList.remove('on')); }
  function setWallet(n, v) {
    const from = n.wallet; n.wallet = v;
    anim(600, p => { n.w.textContent = money(from + (v - from) * ease(p)); });
    flash(n.w, 'flash', 900);
  }
  function earn(n, amt) { n.earned += amt; n.earnEl.textContent = '+' + money(n.earned); flash(n.earnEl, 'flash', 900); }

  /* ── 脉冲与连线（SVG）─────────────────────────────────────── */
  function pulse(a, b, label, color, dur, accts) {
    if (quiet) return;
    const ea = nodeEl(a), eb = nodeEl(b);
    if (!ea || !eb || ea === eb) return;
    const p0 = center(ea), p1 = center(eb);
    const mx = (p0.x + p1.x) / 2, my = (p0.y + p1.y) / 2 - Math.min(60, Math.abs(p1.x - p0.x) * 0.12);
    const g = document.createElementNS(SVG_NS, 'g'); g.setAttribute('class', 'pls');
    if (accts) { g.dataset.accts = accts.join(' '); if (Focus.acct && !accts.includes(Focus.acct)) g.setAttribute('class', 'pls dim'); }
    const path = document.createElementNS(SVG_NS, 'path'); path.setAttribute('class', 'trail'); path.setAttribute('stroke', color);
    path.setAttribute('d', `M${p0.x},${p0.y} Q${mx},${my} ${p1.x},${p1.y}`);
    const c = document.createElementNS(SVG_NS, 'circle'); c.setAttribute('class', 'pulse'); c.setAttribute('r', 5); c.setAttribute('fill', color);
    const t = document.createElementNS(SVG_NS, 'text'); t.setAttribute('class', 'plabel'); t.setAttribute('fill', color); t.textContent = label || '';
    g.appendChild(path); g.appendChild(c); g.appendChild(t); $('pnWires').appendChild(g);
    const D_ = dur || 900, total = path.getTotalLength();
    anim(D_ + 500, p => {
      const q = Math.min(p * (D_ + 500) / D_, 1), pt = path.getPointAtLength(total * ease(q));
      c.setAttribute('cx', pt.x); c.setAttribute('cy', pt.y); c.setAttribute('opacity', q < 1 ? 1 : 1 - (p - D_ / (D_ + 500)) * 3);
      t.setAttribute('x', pt.x + 8); t.setAttribute('y', pt.y - 7); t.setAttribute('opacity', q < 1 ? 1 : 0);
      path.setAttribute('opacity', 0.55 * (1 - p));
    }, () => g.remove());
  }
  function link(n, sid) {
    unlink(n);
    const eb = nodeEl(sid); if (!eb) return;
    const l = document.createElementNS(SVG_NS, 'line'); l.setAttribute('class', 'link');
    l.dataset.accts = [n.uid, (svcNode(sid) || {}).owner || ''].join(' ');
    $('pnWires').insertBefore(l, $('pnWires').firstChild);
    n.link = l; n.provider = Names.key(sid); links.push(n); redrawLink(n);
  }
  function redrawLink(n) {
    if (!n.link) return; const eb = nodeEl(n.provider); if (!eb) return;
    const p0 = edge(n.el, 'right'), p1 = edge(eb, 'left');
    n.link.setAttribute('x1', p0.x); n.link.setAttribute('y1', p0.y); n.link.setAttribute('x2', p1.x); n.link.setAttribute('y2', p1.y);
  }
  function unlink(n) { if (!n.link) return; n.link.remove(); n.link = null; const i = links.indexOf(n); if (i >= 0) links.splice(i, 1); }
  function breakLink(n) { if (!n.link) return; const l = n.link; l.setAttribute('class', 'link broken'); n.link = null; const i = links.indexOf(n); if (i >= 0) links.splice(i, 1); later(1800, () => l.remove()); }
  function hitlines(ids) {
    if (quiet) return;
    const p0 = center($('pnRegistry'));
    ids.forEach(sid => {
      const node = svcNode(sid); if (!node) return;
      flash(node.el, 'hit', 2600);
      const p1 = center(node.el);
      const l = document.createElementNS(SVG_NS, 'line'); l.setAttribute('class', 'hitline');
      l.setAttribute('x1', p0.x); l.setAttribute('y1', p0.y); l.setAttribute('x2', p1.x); l.setAttribute('y2', p1.y);
      $('pnWires').appendChild(l);
      anim(2600, p => l.setAttribute('opacity', p < 0.15 ? p / 0.15 : 1 - (p - 0.15) / 0.85), () => l.remove());
    });
  }

  /* ── 事件 → 画面 ─────────────────────────────────────────── */
  function providerOfCard(cd) { const c = cd && cases.byCard[cd]; return c ? c.provider : null; }
  function apply(ev) {
    if (!ready) return;
    const op = ev.op, s = Names.key(ev.sender), r = Names.key(ev.receiver), d = ev.detail || {};
    const money_ = replay || (ev.seq || 0) > baseSeq;            // 实时模式下初始余量已含历史事件，只对新事件记钱
    quiet = !money_;                                               // 历史事件：只恢复状态，不画脉冲、不闪
    const A = N.asst[s] || N.asst[r] || N.asst[d.assistant] || (N.byUser[s] && N.asst[N.byUser[s]]) || (N.byUser[r] && N.asst[N.byUser[r]]) || null;
    if(d.evidenceTrust==='verified'&&d.signatures){
      if(d.signatures['5']?.some(Boolean)&&ev.cardDigest)anchoredCards.add(ev.cardDigest);
      if(A)Object.entries(d.signatures).forEach(([k,value])=>sig(A,k,Array.isArray(value)?value.some(Boolean):!!value));
    }
    if(ev.kind==='message'&&d.messageId)observedMessages.add(d.messageId);
    const accts = Focus.accountsOf(ev);
    switch (op) {
      case 'message.queued': { const from = nodeEl(s), to = nodeEl(r); if (from && to) { pulse(from, $('pnMessages'), '结构化消息', COLORS.agent, 700, accts); later(700, () => pulse($('pnMessages'), to, d.interface || '收取', COLORS.agent, 700, accts)); } break; }
      case 'user.register': break;
      case 'assistant.register': if (!N.asst[r]) { const u = (USERS || []).find(x => x.id === s); if (u) mkAssistant({ id: r, name: ev.outcome }, u); } break;
      case 'service.register': {
        if (!N.svc[r.replace(/^.*\//, '')] && !svcNode(r)) { const sid = Names.key(r); const reg = (REGISTRY || []).find(x => x.id === sid); if (reg) mkService(reg, catIndex()); }
        break;
      }
      case 'wallet.adjust': {
        if (!money_) break;
        const aid = N.byUser[r];
        if (aid) { pulse('pay', aid, `+${money(ev.amount)}`, COLORS.pay, 800, accts); later(800, () => setWallet(N.asst[aid], N.asst[aid].wallet + Number(ev.amount || 0))); }
        else pendingCredit[r] = (pendingCredit[r] || 0) + Number(ev.amount || 0);
        break;
      }
      case 'template.endorse': { const node = svcNode(s); if (money_) Stat.endorsed++; if (node) pulse(node.el, $('pnTemplates'), '合同模板', COLORS.provider, 700, accts); flash($('pnTemplates'), 'glow', 400); break; }
      case 'need.create': if (A) { resetSigs(A); unlink(A); pulse(A.el.querySelector('.ava'), A.el.querySelector('.state'), '需求', COLORS.user, 500, accts); setState(A, (d.text || '收到需求').slice(0, 24), 'info'); } break;
      case 'need.start': if (A) setState(A, '助手处理中', 'info'); break;
      case 'discovery.search': {
        if (!A) break;
        Stat.searches++;
        pulse(A.el, $('pnRegistry'), '检索', COLORS.agent, 800, accts); flash($('pnRegistry'), 'glow', 1200); setState(A, '服务发现（A2X）', 'info');
        const ids = d.ids || [];
        later(850, () => hitlines(ids));
        break;
      }
      case 'template.get': if (A) pulse(A.el, r, '取模板', COLORS.agent, 700, accts); break;
      case 'template.get.result': if (A) { pulse(s, A.el, '模板', COLORS.provider, 600, accts); setState(A, '比较合同模板', 'info'); } break;
      case 'order.signed': if (A) { link(A, d.provider || providerOfCard(ev.cardDigest)); sig(A, 1); Stat.signed++; setState(A, '签1 采纳合同卡', 'info'); } break;
      case 'card.countersign': if (A) pulse(A.el, r, '请求复签', COLORS.agent, 800, accts); break;
      case 'card.countersign.result': if (A && ev.outcome === 'completed') { pulse(s, A.el, '签2 复签', COLORS.provider, 700, accts); sig(A, 2); setState(A, '服务方已复签（签2）', 'ok'); } break;
      case 'order.declined': {
        if (!A) break;
        const node = svcNode(d.provider || A.provider); const busy = /接满/.test(d.reason || '');
        if (node) { setSvcState(node, busy ? '已接满' : '拒绝', 'busy'); pulse(node.el, A.el, busy ? '已接满 · 拒绝' : '拒绝', COLORS.bad, 800, accts); later(2600, () => setSvcState(node, '在线', 'ok')); }
        breakLink(A); sig(A, 1, false); Stat.declined++; setState(A, busy ? '被拒：对方已接满 · 换一家' : '被拒 · 换一家', 'bad'); break;
      }
      case 'request.submit': if (A) { pulse(A.el, r, '提案卡', COLORS.agent, 800, accts); setState(A, '提案型：提交提案卡', 'info'); } break;
      case 'request.returned': {
        if (!A) break;
        const node = svcNode(d.provider || r); const busy = /接满/.test(d.reason || '');
        if (node) { setSvcState(node, busy ? '已接满' : '退回', 'busy'); pulse(node.el, A.el, busy ? '已接满 · 退回提案卡' : '退回提案卡', COLORS.bad, 800, accts); later(2600, () => setSvcState(node, '在线', 'ok')); }
        Stat.declined++; setState(A, busy ? '提案卡被退回：对方已接满 · 换一家' : '提案卡被退回 · 换一家', 'bad'); break;
      }
      case 'card.endorse': { const a = Object.values(N.asst).find(x => x.provider === s) || null; pulse(s, $('pnNotary'), '签3 验收器快照', COLORS.market, 700, accts); Stat.sig3++; flash($('pnNotary'), 'glow', 600); if (a) sig(a, 3); break; }
      case 'request.submit.result': if (A) { pulse(s, A.el, '合同卡 · 报价', COLORS.provider, 700, accts); sig(A, 2); setState(A, '服务方拟卡报价', 'info'); } break;
      case 'ap2.pay': if (A) { pulse(A.el, 'pay', `付款 ${money(ev.amount)}`, COLORS.pay, 800, accts); flash($('pnPay'), 'glow', 900); } break;
      case 'transfer': {
        const amt = Number(ev.amount || 0); Stat.transfers++; Stat.volume += amt;
        if (!money_) break;
        const aid = N.byUser[s]; if (aid) later(400, () => setWallet(N.asst[aid], N.asst[aid].wallet - amt));
        const node = svcNode(providerOfCard(ev.cardDigest) || '') || null;
        if (node) { pulse('pay', node.el, `+${money(amt)}`, COLORS.pay, 800, accts); later(800, () => earn(node, amt)); }
        break;
      }
      case 'work.start': if (A) pulse(A.el, r, '开工', COLORS.agent, 800, accts); break;
      case 'work.start.result': { const a = Object.values(N.asst).find(x => x.provider === s); if (a) pulse(s, a.el, '接单回执', COLORS.provider, 600, accts); break; }
      case 'order.started': if (A) { setState(A, '已开工 · 等待交付', 'info'); setSvcState(svcNode(A.provider), '工作中', 'work'); } break;
      case 'anchor.watch': if (A) pulse(A.el, $('pnNotary'), '订阅存证', COLORS.agent, 600, accts); break;
      case 'step.authorize': { const a = Object.values(N.asst).find(x => x.provider === r) || A; if (a) { pulse(r, a.el, '环节授权请求', COLORS.provider, 800, accts); setState(a, '环节授权：上升用户', 'warn'); } break; }
      case 'order.step-answered': if (A) { pulse(A.el, A.provider, d.granted ? '允许' : '拒绝', COLORS.user, 700, accts); setState(A, '已授权 · 继续交付', 'info'); } break;
      case 'delivery.anchor': { const a = Object.values(N.asst).find(x => x.provider === s); pulse(s, $('pnNotary'), '交付留痕 签4 · 签5', COLORS.provider, 800, accts); Stat.sig5++; flash($('pnNotary'), 'glow', 600); setSvcState(svcNode(s), '已交付', 'ok'); if (a) { sig(a, 4); sig(a, 5); setState(a, '收到存证通知', 'info'); } break; }
      case 'delivery.fetch': if (A) pulse(A.el, r, '取件', COLORS.agent, 700, accts); break;
      case 'order.fetched': if (A) setState(A, '签收 · 运行验收器', 'info'); break;
      case 'order.verified': if (A) { sig(A, 6); setState(A, ev.outcome === 'pass' ? '验收器通过（签6）' : '验收器判不通过', ev.outcome === 'pass' ? 'ok' : 'bad'); } break;
      case 'order.accepted': if (A) { sig(A, 7); setState(A, '签7 · 已接受', 'ok'); } break;
      case 'arbitration.ruling': { const a = Object.values(N.asst).find(x => x.provider && /不通过/.test(x.state)) || null; pulse($('pnLedger'), a ? a.el : null, '裁定文书', COLORS.bad, 900, accts); flash($('pnLedger'), 'glow', 900); break; }
      case 'order.arbitrated': if (A) { Stat.arbitrated++; setState(A, '已裁定 · ' + (OUTCOME[ev.outcome] || ev.outcome || ''), 'warn'); const n = svcNode(A.provider); setSvcState(n, '裁定 · 责任方', 'warn'); if (n) { n.done++; n.rep.textContent = ` · ${n.done} 单`; } break; }
      case 'card.close': { pulse(s, $('pnLedger'), '结清', COLORS.provider, 800, accts); flash($('pnLedger'), 'glow', 600); break; }
      case 'order.completed': { const a = Object.values(N.asst).find(x => x.provider === s) || A; Stat.completed++; if (a) setState(a, '已结清 ✓', 'ok'); const n = svcNode(s); if (n) { n.done++; n.okc++; setSvcState(n, '在线', 'ok'); n.rep.textContent = ` · ${n.done} 单`; } break; }
      case 'need.failed': if (A) setState(A, '未成', 'bad'); break;
      case 'order.terminated': if (A) { setState(A, '已解除', 'warn'); breakLink(A); } break;
      case 'confirm.request': { const aid = N.byUser[r]; if (aid) { flash(N.asst[aid].el, 'ask', 2500); setState(N.asst[aid], '等用户确认：' + (OUTCOME[ev.outcome] || ev.outcome), 'warn'); } break; }
      case 'confirm.answer': { const aid = N.byUser[s]; if (aid) pulse(N.asst[aid].el.querySelector('.ava'), N.asst[aid].el.querySelector('.state'), ev.outcome === 'granted' ? '允许' : '拒绝', COLORS.user, 500, accts); break; }
      default: break;
    }
    quiet = false;
    renderCounters();
  }
  function renderCounters() {
    const rows=serverStats&&!replay?[['已接入助手',serverStats.assistants],['服务',serverStats.services],['今日委托记录',serverStats.orders_today],['今日过账',`${serverStats.transfers_today} 笔 · ${money(serverStats.volume_today)}`],['已结清',serverStats.completed],['已观察消息',observedMessages.size]]:[['助手', Stat.asst], ['服务', Stat.svc], ['检索', Stat.searches], ['定约', Stat.signed], ['被拒换家', Stat.declined], ['过账', `${Stat.transfers} 笔 · ${money(Stat.volume)}`], ['结清', Stat.completed], ['裁定', Stat.arbitrated]];
    $('pnCounters').innerHTML = rows
      .map(([k, v]) => `<span>${k}<b>${v}</b></span>`).join('');
    $('pnRegStat').textContent = `A2X 注册表 · 服务 ${Stat.svc} · 分类树导航`;
    $('pnTplStat').textContent = `已签认 ${Stat.endorsed} 份`;
    $('pnNotaryStat').textContent = `已观察交付存证 ${anchoredCards.size} 单 · 签名链可核验`;
    $('pnLedgerStat').textContent = `结清 ${Stat.completed} · 裁定 ${Stat.arbitrated}`;
    $('pnPayStat').textContent = `过账 ${Stat.transfers} 笔 · 合计 ${money(Stat.volume)}`;
    $('pnMessageStat').textContent=`已观察 ${observedMessages.size} 条消息 · 正文不进入全局广播`;
  }

  /* ── 初始化 ─────────────────────────────────────────────── */
  let CATS = {};
  function catIndex() { return CATS; }
  function init(users, registry, categories, opts) {
    opts = opts || {};
    observedMessages.clear();anchoredCards.clear();serverStats=null;
    replay = !!opts.replay; baseSeq = opts.baseSeq || 0; ready = false;
    ['pnAsstList', 'pnBlocks', 'pnWires'].forEach(id => { $(id).innerHTML = ''; });
    Object.keys(N.asst).forEach(k => delete N.asst[k]); Object.keys(N.svc).forEach(k => delete N.svc[k]); Object.keys(N.byUser).forEach(k => delete N.byUser[k]);
    Object.keys(Stat).forEach(k => { Stat[k] = 0; }); links.length = 0; Anims.length = 0; Timers.length = 0;
    CATS = {}; (categories || []).forEach(c => { CATS[c.id] = c; });
    const KIND_ORDER = { external: 0, virtual: 1, isc: 2, isp: 3 };
    [...users].sort((a, b) => (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || String(a.username).localeCompare(String(b.username)))
      .forEach(u => {
        const assistants = (u.agents || []).filter(a => a.role === 'assistant');
        if (!assistants.length && u.kind === 'isc') {
          const node = mkAssistant({id:'account:'+u.id,name:'未接入助手'}, u);
          setState(node,'账号空闲');
        }
        assistants.forEach(a => mkAssistant(a, replay ? {...u,balance:0} : u));
      });
    const svcs = [...registry].sort((a, b) => String(a.category || '').localeCompare(String(b.category || '')) || String(a.name).localeCompare(String(b.name)));
    svcs.forEach(s => mkService(s, CATS));
    Stat.endorsed = svcs.length;
    renderCounters();
    ready = true;
  }
  /** 从已载入的卡状态恢复连线与签名（实时模式接入时，正在进行的单不必等事件重放） */
  function restore(caseList) {
    caseList.forEach(c => {
      const A = N.asst[c.assistant]; if (!A || !c.provider || !c.cd) return;
      if (/进行中|已结清|已裁定/.test(c.state) && c.state !== '被拒绝') { link(A, c.provider); }
      [1, 2, 3].forEach(k => sig(A, k, !!c.sigs[k]));
      [4, 5, 6, 7].forEach(k => sig(A, k, (c.sigs[k] || []).some(Boolean)));
      if (c.state === '已结清') setState(A, '已结清 ✓', 'ok'); else if (c.state === '进行中') setState(A, '进行中', 'info'); else if (/裁定/.test(c.state)) setState(A, c.state, 'warn');
    });
  }
  function tick(n) {
    if (!ready || !$('view-panorama').classList.contains('on')) return;
    for (let i = Timers.length - 1; i >= 0; i--) if (Timers[i].at <= n) { const t = Timers.splice(i, 1)[0]; t.fn(); }
    for (let i = Anims.length - 1; i >= 0; i--) {
      const a = Anims[i], p = Math.min(Math.max((n - a.start) / a.dur, 0), 1);
      a.fn(p);
      if (p >= 1) { Anims.splice(i, 1); if (a.done) a.done(); }
    }
    touched = touched.filter(t => n - t.at < 1500);
    const live = new Set(touched.map(t => t.el));
    document.querySelectorAll('.pn-asst.live, .pn-svc.live').forEach(el => { if (!live.has(el)) el.classList.remove('live'); });
    live.forEach(el => el.classList.add('live'));
    links.forEach(redrawLink);
  }
  function focus(acct) {
    document.querySelectorAll('#pnWires .pls').forEach(g => g.setAttribute('class', 'pls' + (acct && !(g.dataset.accts || '').split(' ').includes(acct) ? ' dim' : '')));
  }
  return { init, apply, tick, restore, focus, N, Stat, setStats(s){serverStats=s;renderCounters();} };
})();
