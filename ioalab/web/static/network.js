/* Account-scoped network. Ambient nodes are geometry only, never other users' data. */
window.IoaNetwork = (() => {
  const escape = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const components = {
    assistant: ['我的九问', '需求与本地验收', '九问在你的设备运行。由本地 IoA SDK 签约、取件，在 WASM 沙箱验收，确认后签字。'],
    a2x: ['A2X 注册中心', '搜索 · 筛选服务', '按领域发现服务，读取公示合同模板，比较输入要求、报价与验收标准。'],
    contract: ['A2C 合同中心', '定约 · 签名 · 存证', '核验合同与签名链，记录交付存证；不代替个人助手生产或验收。'],
    message: ['智能体消息中心', '免费 · 定向 · 离线', '按账号查询已获授权的接口，核验 inputSchema 后投递。接收方上线处理并回执。'],
    payment: ['模拟支付', '读卡过账', '使用虚拟币演示。付款依据是合同金额与相应签名，已付金额以实际过账记录为准。'],
    service: ['服务方智能体', '协商 · 生产 · 交付', '服务在独立进程运行，按合同生产交付成果；官方九问服务按需启动。'],
  };
  let selected = 'assistant', latest = null, selectedOrder = null;
  const done = new Set(['completed','finished','arbitrated','client-terminated','terminated']);
  const positions = {assistant:[15,43],a2x:[43,18],contract:[52,47],message:[42,79],payment:[72,80],service:[83,34]};
  const links = [['assistant','a2x'],['a2x','service'],['assistant','contract'],['contract','service'],['assistant','message'],['contract','payment'],['payment','service']];
  function detail() {
    const c=components[selected], box=document.getElementById('component-detail');
    let extra='';
    if (selected==='message') extra=`已登记 ${latest.interfaces.length} 个接口 · ${latest.messages.length} 条双方交互记录`;
    else if (selected==='payment') extra=`当前可见委托已付 ${latest.orders.reduce((s,o)=>s+Number(o.paid||0),0).toFixed(2)} 虚拟币`;
    else if (selected==='service') extra=[...new Set(latest.orders.slice(0,3).map(o=>o.providerName))].join(' · ')||'从 A2X 发现适合当前需求的服务';
    else if (selected==='contract') extra=`${latest.orders.length} 笔可见委托 · 合同卡与签名可从记录详情展开`;
    box.innerHTML=`<b>${escape(c[0])}</b><span>${escape(c[2])}</span><small>${escape(extra)}</small>`;
    document.querySelectorAll('[data-graph-node]').forEach(n=>n.classList.toggle('selected',n.dataset.graphNode===selected));
  }
  function select(key) { if(components[key]){selected=key;detail();} }
  function render(data) {
    latest=data;
    const map=document.getElementById('network-map');
    // Build only once: selection and background animations survive polling.
    if (!map.children.length) {
      map.innerHTML=`<div class="ambient-field" aria-hidden="true">${Array.from({length:14},(_,i)=>`<i style="left:${4+(i*19)%93}%;top:${7+(i*31)%88}%;animation-delay:-${i/3}s"></i>`).join('')}</div><svg class="network-links" viewBox="0 0 1000 300" preserveAspectRatio="none" aria-hidden="true">${links.map(([a,b],i)=>{const p=positions[a],q=positions[b];const d=`M ${p[0]*10},${p[1]*3} Q ${(p[0]+q[0])*5},${Math.min(p[1],q[1])*3-18} ${q[0]*10},${q[1]*3}`;return `<path d="${d}"/><circle r="3" class="network-packet"><animateMotion dur="${3+i*.3}s" repeatCount="indefinite" begin="-${i}s" path="${d}"/></circle>`;}).join('')}</svg>${Object.entries(components).map(([key,c])=>`<button class="network-node node-${key}" data-graph-node="${key}" style="left:${positions[key][0]}%;top:${positions[key][1]}%" aria-label="${c[0]}详情"><i>${({assistant:'问',a2x:'寻',contract:'约',message:'信',payment:'付',service:'工'})[key]}</i><b>${c[0]}</b><small>${c[1]}</small></button>`).join('')}`;
      map.querySelectorAll('[data-graph-node]').forEach(n=>n.onclick=()=>select(n.dataset.graphNode));
    }
    detail();journey();
  }
  function journey() {
    const o=latest.orders.find(x=>x.card_digest===selectedOrder)||latest.orders[0],s=o?.signatures||{};
    const events=latest.events||[],ops=new Set(events.filter(e=>!e.card_digest||e.card_digest===o?.card_digest).map(e=>e.op));
    const steps=[
      ['发起','assistant',!!o||ops.has('need.create'),'参与方上报'],
      ['A2X 搜索','a2x',ops.has('discovery.search'),'过程记录'],
      ['筛选服务','a2x',ops.has('discovery.compared'),'参与方上报'],
      ['协商报价','service',!!s['2']||ops.has('order.proposed'),'报价/合同记录'],
      ['定约','contract',!!s['1'],'已核验签名'],
      ['交付','service',!!s['5']?.some(Boolean),'已核验存证签名'],
      ['本地验收','assistant',!!s['7']?.some(Boolean),'本地验收确认签名'],
      ['模拟支付','payment',Number(o?.total)>0&&Number(o?.paid)>=Number(o?.total),'实际过账记录'],
    ];
    document.getElementById('journey').innerHTML=steps.map(([name,node,yes,trust],i)=>`<button class="journey-step ${yes?'observed':''}" data-stage="${node}" title="${trust}：${yes?'已观察':'尚未观察'}"><i>${yes?'✓':String(i+1).padStart(2,'0')}</i><b>${name}</b><small>${yes?trust:'尚未观察'}</small></button>`).join('');
    document.querySelectorAll('[data-stage]').forEach(n=>n.onclick=()=>select(n.dataset.stage));
    document.getElementById('journey-status').textContent=o?(done.has(o.state)?'已结束委托':'当前委托')+' · '+o.providerName:'等待九问发起需求';
    document.getElementById('journey-evidence').innerHTML=o?`<span>合同总额 <b>${escape(o.total)}</b></span><span>实际已付 <b>${escape(o.paid||'0.00')}</b></span><small>虚拟币 · 验收执行位于个人助手本地</small>`:'<small>记录来自真实 SDK 调用与平台证据；未观察的步骤不会补成已完成。</small>';
  }
  return {render,select,done,chooseOrder(cd){selectedOrder=cd;journey();}};
})();
