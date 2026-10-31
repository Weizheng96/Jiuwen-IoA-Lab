/* Account-scoped map. Grey geometry is synthetic and never contains other accounts' data. */
window.IoaNetwork = (() => {
  const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const nodes = {assistant:'ownAssistant',a2x:'pnRegistry',contract:'pnMarket',message:'pnMessages',payment:'pnPay',service:'ownService'};
  const components = {
    assistant:['我的个人助手','','在用户自己的九问运行，由本地 SDK 签约、取件与验收。'],
    service:['相关专业服务','','独立服务按合同生产和交付，个人助手按约验收。'],
  };
  NetworkMap.components.concat(NetworkMap.subs).forEach(([id,title,protocol,hint]) => {
    const key=Object.keys(nodes).find(k=>nodes[k]===id) || id;
    nodes[key]=id; components[key]=[title,protocol,hint];
  });
  let selected='assistant',latest=null,selectedOrder=null,lastSeq=null,messageStates=new Map();
  const done = new Set(['completed','finished','arbitrated','client-terminated','terminated']);
  const live=[], rng=NetworkMap.random(4721), peerSlots=new Map();
  let nextAmbient=0;
  const el=key=>document.getElementById(nodes[key] || key);
  function detail(){
    const c=components[selected],box=document.getElementById('component-detail');
    const extra=selected==='message' ? `${latest.interfaces.length} 个自定义接口 · ${latest.messages.length} 条双方消息` : selected==='contract' ? `${latest.orders.length} 笔本人委托` : '';
    box.innerHTML=`<b>${escape(c[0])}</b><span>${escape(c[2])}</span><small>${escape(extra)}</small>`;
    document.querySelectorAll('[data-graph-node]').forEach(n=>n.classList.toggle('selected',n.dataset.graphNode===selected));
  }
  function select(key){if(components[key]){selected=key;detail();}}
  function pulse(a,b,label,color='#79dfd9',ambient=false){
    if(!a||!b||a===b||document.hidden||document.getElementById('assistant-view').hidden)return;
    const item=NetworkMap.flow(document.getElementById('accountWires'),a,b,label,color);
    item.group.dataset.activity=ambient?'background':'own';
    live.push({item,at:performance.now(),duration:ambient?460+rng()*280:700});
  }
  function animate(now){
    for(let i=live.length-1;i>=0;i--){const f=live[i],p=(now-f.at)/f.duration;f.item.paint(p);if(p>=1)live.splice(i,1);}
    if(now>=nextAmbient){
      nextAmbient=now+240+rng()*520;
      const a=document.querySelectorAll('.ambient-assistant'),s=document.querySelectorAll('.ambient-service');
      if(a.length&&s.length){
        const from=a[Math.floor(rng()*a.length)],other=a[Math.floor(rng()*a.length)];
        if(rng()<.54) pulse(from,s[Math.floor(rng()*s.length)],'', '#7b8593',true);
        else pulse(from,other,'','#7b8593',true);
      }
    }
    requestAnimationFrame(animate);
  }
  function build(map){
    map.className='pn account-network';
    map.innerHTML='<div class="account-left"><button id="ownAssistant" class="account-actor" data-graph-node="assistant"><b></b><small>我的个人助手</small></button><small class="anonymous-label">个人助手网络</small><div class="anonymous-grid">'+Array.from({length:16},(_,i)=>`<span class="anonymous-node ambient-assistant" aria-label="匿名个人助手">助手 ${String(i+1).padStart(2,'0')}</span>`).join('')+'</div></div><div class="pn-mid account-centers"></div><div class="account-right"><button id="ownService" class="account-actor" data-graph-node="service"><b>相关专业服务</b><small>尚无关联委托</small></button><div id="accountPeers"></div><small class="anonymous-label">公开专业服务</small><div id="public-network-services" class="public-service-list"></div></div><svg id="accountWires" aria-hidden="true"></svg>';
    NetworkMap.mount(map.querySelector('.account-centers'));
    Object.entries(nodes).forEach(([key,id])=>{if(document.getElementById(id))document.getElementById(id).dataset.graphNode=key;});
    map.addEventListener('click',e=>{const n=e.target.closest('[data-graph-node]');if(n)select(n.dataset.graphNode);});
    requestAnimationFrame(animate);
  }
  function updateActors(){
    const name=latest.account.display_name||latest.account.username;
    el('assistant').querySelector('b').textContent=name+'的个人助手';
    el('assistant').querySelector('small').textContent=latest.identities.some(x=>x.role==='assistant')?'已登记 · 本地九问':'等待九问接入';
    const order=latest.orders.find(o=>o.card_digest===selectedOrder)||latest.orders[0];
    el('service').querySelector('b').textContent=order?.providerName||latest.intents[0]?.providerName||'相关专业服务';
    el('service').querySelector('small').textContent=order ? (done.has(order.state)?'已结束委托':'当前委托') : latest.intents.length?'委托意向处理中':'从 A2X 发现服务';
    const services=(latest.publicServices||[]).filter(s=>s.url!==order?.provider_id).slice(0,18);
    const serviceList=document.getElementById('public-network-services'),signature=services.map(s=>s.id+s.name).join('|');
    if(serviceList.dataset.signature!==signature){
      serviceList.dataset.signature=signature;
      serviceList.innerHTML=services.map(s=>`<button class="public-service ambient-service" title="${escape(s.description)}" data-public-service="${escape(s.id)}" data-provider-url="${escape(s.url)}">${escape(s.name)}</button>`).join('');
      serviceList.querySelectorAll('button').forEach(b=>b.onclick=()=>{const service=services.find(s=>s.id===b.dataset.publicService);select('service');document.getElementById('component-detail').innerHTML=`<b>${escape(service.name)}</b><span>${escape(service.description)}</span><small>公开服务目录 · ${escape(service.status)}</small>`;});
    }
    const related=new Set(latest.orders.map(o=>o.provider_id));
    serviceList.querySelectorAll('[data-provider-url]').forEach(n=>n.classList.toggle('related',related.has(n.dataset.providerUrl)));
    // Only names from authorized two-party messages, never a global directory.
    const names=new Map();
    latest.messages.forEach(m=>{const side=m.sender===latest.account.id?'receiver':'sender';names.set(m[side],m[side+'DisplayName']||m[side+'Username']);});
    const peers=document.getElementById('accountPeers');
    if([...names].map(([id,n])=>id+n).join('|')!==peers.dataset.names){
      peers.dataset.names=[...names].map(([id,n])=>id+n).join('|');peers.replaceChildren();peerSlots.clear();
      [...names].slice(0,2).forEach(([id,n])=>{const b=document.createElement('button');b.className='account-actor peer-actor';b.textContent=n+'的个人助手';b.onclick=()=>select('message');peers.append(b);peerSlots.set(id,b);});
    }
  }
  function ownEvents(){
    const events=latest.events||[];
    if(lastSeq!==null){
      events.filter(e=>e.seq>lastSeq).forEach(e=>{
        const op=e.op,a=el('assistant'),provider=latest.orders.find(o=>o.card_digest===e.card_digest)?.provider_id;
        const main=latest.orders.find(o=>o.card_digest===selectedOrder)||latest.orders[0];
        const s=provider&&provider!==main?.provider_id?[...document.querySelectorAll('[data-provider-url]')].find(n=>n.dataset.providerUrl===provider)||el('service'):el('service');
        if(op==='discovery.search')pulse(a,el('a2x'),'A2X 搜索');
        else if(op==='discovery.compared')pulse(el('a2x'),a,'筛选结果');
        else if(op==='order.proposed')pulse(s,el('contract'),'合约卡草案');
        else if(op==='work.start')pulse(a,s,'开工');
        else if(op==='delivery.anchor')pulse(s,el('pnNotary'),'交付存证');
        else if(op==='delivery.fetch'||op==='order.fetched')pulse(a,s,'取件');
        else if(op==='order.verified')pulse(el('pnNotary'),a,'本地验收结果');
        else if(op==='order.accepted')pulse(a,el('contract'),'验收签字');
        else if(op==='transfer'||op==='ap2.pay')pulse(el('payment'),s,'读卡过账','#efd282');
      });
      latest.messages.forEach(m=>{
        const prior=messageStates.get(m.id),own=latest.account.id,out=m.sender===own,peer=peerSlots.get(out?m.receiver:m.sender);
        const before=prior||'new';
        if(before===m.state)return;
        if(!prior) pulse(out?el('assistant'):peer,el('message'),'结构化消息');
        if(['processing','completed','rejected'].includes(m.state)&&!['processing','completed','rejected'].includes(prior))pulse(el('message'),out?peer:el('assistant'),'收取 / 处理');
        if(['completed','rejected'].includes(m.state))pulse(out?peer:el('assistant'),el('message'),'处理回执');
      });
    }
    lastSeq=Math.max(lastSeq||0,...events.map(e=>e.seq));
    messageStates=new Map(latest.messages.map(m=>[m.id,m.state]));
  }
  function render(data){
    latest=data;const map=document.getElementById('network-map');if(!map.children.length)build(map);
    updateActors();ownEvents();detail();journey();
  }

  function journey() {
    const o =
        latest.orders.find((x) => x.card_digest === selectedOrder) ||
        latest.orders[0],
      s = o?.signatures || {};
    const events = latest.events || [],
      ops = new Set(
        events
          .filter((e) => !e.card_digest || e.card_digest === o?.card_digest)
          .map((e) => e.op),
      );
    const steps = [
      ["发起", "assistant", !!o || ops.has("need.create"), "参与方上报"],
      ["A2X 搜索", "a2x", ops.has("discovery.search"), "过程记录"],
      ["选择服务", "a2x", !!o?.effective || ops.has("discovery.compared"), "合同指向的所选服务"],
      [
        "协商报价",
        "service",
        !!s["2"] || ops.has("order.proposed"),
        "报价/合同记录",
      ],
      ["定约", "contract", !!o?.effective, "原A2C成约证据"],
      ["交付", "service", !!s["5"]?.some(Boolean), "已核验存证签名"],
      ["验收确认", "assistant", !!s["7"]?.some(Boolean), "已核验验收签名"],
      [
        "模拟支付",
        "payment",
        Number(o?.total) > 0 && Number(o?.paid) >= Number(o?.total),
        "实际过账记录",
      ],
    ];
    document.getElementById("journey").innerHTML = steps
      .map(
        ([name, node, yes, trust], i) =>
          `<button class="journey-step ${yes ? "observed" : ""}" data-stage="${node}" title="${trust}：${yes ? "已观察" : "尚未观察"}"><i>${yes ? "✓" : String(i + 1).padStart(2, "0")}</i><b>${name}</b><small>${yes ? trust : "尚未观察"}</small></button>`,
      )
      .join("");
    document
      .querySelectorAll("[data-stage]")
      .forEach((n) => (n.onclick = () => select(n.dataset.stage)));
    document.getElementById("journey-status").textContent = o
      ? (done.has(o.state) ? "已结束委托" : "当前委托") + " · " + o.providerName
      : "等待九问发起需求";
    document.getElementById("journey-evidence").innerHTML = o
      ? `<span>合同总额 <b>${escape(o.total)}</b></span><span>实际已付 <b>${escape(o.paid || "0.00")}</b></span><small>虚拟币 · 验收执行位于个人助手本地</small>`
      : "<small>记录来自真实 SDK 调用与平台证据；未观察的步骤不会补成已完成。</small>";
  }
  return {
    render,
    select,
    done,
    chooseOrder(cd) {
      selectedOrder = cd;
      updateActors();
      detail();
      journey();
    },
  };
})();
