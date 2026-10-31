/* Owned service operations: selected service persists; amounts come from actual receipts. */
window.ProviderWorkbench=(()=>{
  let selected=null,category=null,last=null;
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const proposalStates=new Set(['forming','offered','drafted','proposed']);
  const ended=new Set(['completed','finished','arbitrated','terminated','client-terminated']);
  const label=s=>({forming:'待确认提案',offered:'待确认提案',completed:'已完成',finished:'已完成',started:'履约中',signed:'已签约',offered:'待采纳',drafted:'待签约',accepted:'已验收',disabled:'已停用',online:'已连接',offline:'未连接',paused:'暂停接新委托',busy:'名额已满',unavailable:'模板不可用',listed:'待公示',terminated:'已解除','client-terminated':'已解除',arbitrated:'已裁定'}[s]||s);
  function rows(list){return list.length?'<div class="provider-table"><div class="table-labels"><span>委托与客户</span><span>履约状态</span><span>合同 / 已收</span><span></span></div>'+list.map(o=>`<article class="provider-order"><div><b>${e(o.clientName||'委托方')}</b><small>${e(o.card_digest.slice(0,23))}…</small></div><span class="badge ${ended.has(o.state)?'done':''}">${e(label(o.state))}</span><div><b>${e(o.total??'报价未知')}</b><small>已收价款 ${e(o.paid)} · 拟卡费 ${e(o.proposalFeePaid)} 虚拟币</small></div><button class="btn" data-order="${e(o.card_digest)}">查看合同</button></article>`).join('')+'</div>':'<div class="provider-empty"><span>—</span><p>暂无记录</p></div>';}
  function intentRows(list){return list.map(r=>`<article class="provider-order"><div><b>${e(r.clientName)}</b><small>委托意向 · 尚未形成合同</small></div><span class="badge">${e(r.phase)}</span><div><b>拟卡费 ${e(r.proposalFeePaid)}</b><small>虚拟币 · 实际已收</small></div><button class="btn" data-provider-intent="${e(r.originMessageId)}">详情</button></article>`).join('');}
  function render(data){
    last=data;const services=data.services,active=services.filter(s=>s.activeDelegations>0).length;
    document.getElementById('provider-metrics').innerHTML=[['已登记服务',services.length],['有在途委托的服务',active],['已完成委托',services.reduce((n,s)=>n+s.completedCount,0)],['实际收入 · 虚拟币',services.reduce((n,s)=>n+Number(s.earned),0).toFixed(2)]].map(([t,v])=>`<div class="metric"><span>${t}</span><b>${v}</b></div>`).join('');
    document.getElementById('taxonomy').innerHTML='<div class="tree-root">A2X 服务目录</div>'+`<button class="category-filter ${category===null?'active':''}" data-category="">全部领域 <small>${services.length}</small></button>`+(data.categories||[]).map(c=>`<button class="category-filter ${category===c.id?'active':''}" data-category="${e(c.id)}">${e(c.name)}<small>${services.filter(s=>s.category===c.id).length}</small></button>`).join('');
    document.querySelectorAll('[data-category]').forEach(b=>b.onclick=()=>{category=b.dataset.category||null;selected=null;render(last);});
    const visible=services.filter(s=>!category||s.category===category);
    if(!visible.some(s=>s.id===selected))selected=visible[0]?.id||null;
    document.getElementById('provider-services').innerHTML=visible.map(s=>`<button class="provider-service ${selected===s.id?'active':''}" data-service="${e(s.id)}"><span><b>${e(s.name)}</b><small>${e(label(s.status))} · ${s.activeDelegations} 笔在途</small></span><strong>${e(s.earned)}<small>虚拟币收入</small></strong></button>`).join('')||'<p class="hint">该领域尚未登记服务</p>';
    document.querySelectorAll('[data-service]').forEach(b=>b.onclick=()=>{selected=b.dataset.service;render(last);document.getElementById('services').scrollTop=0;});
    const service=visible.find(s=>s.id===selected),root=document.getElementById('services');
    if(!service){root.innerHTML='<section class="provider-welcome"><h2>接入专业服务，参与智能体互联网</h2><p>通过服务方SDK登记端点、公示合同模板，即可在这里管理服务与履约。</p><a class="btn" href="/downloads/ioalab-provider-skill.zip">下载服务方技能</a></section>';return;}
    const proposals=service.orders.filter(o=>proposalStates.has(o.state)),current=service.orders.filter(o=>!ended.has(o.state)&&!proposalStates.has(o.state)),history=service.orders.filter(o=>ended.has(o.state));
    const cat=data.categories.find(c=>c.id===service.category)?.name||'其他';
    root.innerHTML=`<section class="provider-hero"><div><div class="eyebrow">${e(cat)} / SERVICE</div><h2>${e(service.name)}</h2><p>${e(service.description||'独立运行的专业服务')}</p><code>${e(service.url)}</code></div><span class="provider-online">${e(label(service.status))}</span></section><div class="provider-summary"><div><span>实际收入</span><strong>${e(service.earned)} <small>虚拟币</small></strong><small>委托价款 ${e(service.commissionEarned)} · 拟卡费 ${e(service.proposalFeesEarned)}</small></div><div><span>当前委托</span><strong>${current.length}</strong></div><div><span>待确认提案</span><strong>${proposals.length}</strong></div><div><span>历史委托</span><strong>${history.length}</strong></div></div>${service.intents.length?`<section class="provider-section"><h3>委托意向记录</h3>${intentRows(service.intents)}</section>`:""}<section class="provider-section"><div class="section-title"><h3>履约工作区</h3><span class="hint">${current.length} 笔待办</span></div>${rows(current)}</section>${proposals.length?`<section class="provider-section"><h3>待确认提案</h3>${rows(proposals)}</section>`:''}<section class="provider-section"><div class="section-title"><h3>完成与历史</h3><span class="hint">逐单保留合同与付款记录</span></div>${rows(history)}</section>`;
    root.querySelectorAll('[data-provider-intent]').forEach(b=>b.onclick=()=>show('委托意向',service.intents.find(r=>r.originMessageId===b.dataset.providerIntent)));
    // index.js binds the initial render; user changes need the same owned-order detail handler.
    root.querySelectorAll('[data-order]').forEach(b=>b.onclick=()=>show('委托详情',service.orders.find(o=>o.card_digest===b.dataset.order)));
  }
  return {render};
})();
