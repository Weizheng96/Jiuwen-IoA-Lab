/* Shared presentation only: no accounts, transactions or business state are created here. */
'use strict';
window.NetworkMap = (() => {
  const components = [
    ['pnIdentity','身份中心','智能体身份码','登记账号及参与方公钥；私钥留在参与方本机。'],
    ['pnMarket','委托中心','智能体委托合同协议','发现服务，比较公开模板，以合同约定交付与验收。'],
    ['pnMessages','消息中心','个人助手交互协议','按账号定向投递免费结构化消息；离线保留，上线收取。'],
    ['pnPay','支付中心','智能体支付授权协议','按用户授权与合同验收依据记录虚拟币过账。'],
    ['pnBounty','悬赏中心','建设中','可发布不指定对象的委托，由个人助手或专业服务主动接取。此处仅展示规划，尚无业务接口。'],
    ['pnIntent','意图中心','用户权限白名单管理','建设中；仅展示规划，当前不参与授权或消息处理。']
  ];
  const subs = [
    ['pnRegistry','A2X 注册中心','pnRegStat','分类树导航 · 服务发现'],
    ['pnTemplates','合同模板公示','pnTplStat','交付件 · 要价 · 验收判据'],
    ['pnContracts','合同记录','pnContractStat','协商 · 定约 · 履约状态'],
    ['pnNotary','存证与签名','pnNotaryStat','摘要 · 时间戳 · 签名链'],
    ['pnLedger','信誉日志','pnLedgerStat','已完成合同的信誉依据'],
    ['pnArbitration','仲裁中心','pnArbitrationStat','按约复算 · 出具文书']
  ];
  function mount(root) {
    root.innerHTML = components.map(([id,title,protocol,description]) => {
      const content = id==='pnMarket' ? '<div class="subs">'+subs.map(([key,name,stat,hint])=>`<button class="sub" id="${key}" type="button"><b>${name}</b><span id="${stat}">${hint}</span></button>`).join('')+'</div>' : `<span class="map-stat" id="${{pnIdentity:'pnIdentityStat',pnMessages:'pnMessageStat',pnPay:'pnPayStat',pnBounty:'pnBountyStat',pnIntent:'pnIntentStat'}[id]}">${id==='pnBounty'?'面向全网发布 · 自主接取':description}</span>`;
      return `<section class="pn-infra ${id==='pnBounty'||id==='pnIntent'?'construction':''}" id="${id}" tabindex="0" data-map-component><div class="title">${title}<small>${protocol}</small></div>${content}</section>`;
    }).join('')+'<aside id="mapInfo" hidden></aside>';
    let selected;
    function clear() { root.querySelectorAll('.component-focus').forEach(n=>n.classList.remove('component-focus'));root.closest('.pn').classList.remove('component-selected');root.querySelector('#mapInfo').hidden=true;selected=null; }
    root.addEventListener('click', event=>{
      const el=event.target.closest('.sub,[data-map-component]');if(!el)return;
      if(selected===el){clear();return;}clear();selected=el;el.classList.add('component-focus');root.closest('.pn').classList.add('component-selected');
      const item=components.find(x=>x[0]===el.id)||subs.find(x=>x[0]===el.id);
      const info=root.querySelector('#mapInfo');info.textContent=item[1]+' · '+(item[3]||item[2]);info.hidden=false;
    });
    document.addEventListener('keydown',event=>{if(event.key==='Escape')clear();});
  }
  const NS='http://www.w3.org/2000/svg';
  function svg(tag,attrs){const node=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));return node;}
  // Identical linear travel and hard removal in real monitoring and narrated animation.
  function flow(layer,a,b,label,color='#76dce6') {
    const base=layer.getBoundingClientRect(),x=a.getBoundingClientRect(),y=b.getBoundingClientRect();
    const sx=layer.viewBox.baseVal.width?layer.viewBox.baseVal.width/base.width:1;
    const sy=layer.viewBox.baseVal.height?layer.viewBox.baseVal.height/base.height:1;
    const p={x:(x.x+x.width/2-base.x)*sx,y:(x.y+x.height/2-base.y)*sy},q={x:(y.x+y.width/2-base.x)*sx,y:(y.y+y.height/2-base.y)*sy};
    const group=svg('g',{class:'pls'}),path=svg('path',{d:`M${p.x},${p.y} Q${(p.x+q.x)/2},${(p.y+q.y)/2-28} ${q.x},${q.y}`,fill:'none',stroke:color,'stroke-width':1.7});
    const dot=svg('circle',{r:4.5,fill:color}),text=svg('text',{fill:color,'font-size':14,'paint-order':'stroke',stroke:'#0a1424','stroke-width':4});text.textContent=label;group.append(path,dot,text);layer.append(group);
    const length=path.getTotalLength();
    return {group,paint(progress){if(progress>=1){group.remove();return;}const at=path.getPointAtLength(length*Math.max(0,progress));dot.setAttribute('cx',at.x);dot.setAttribute('cy',at.y);text.setAttribute('x',at.x+7);text.setAttribute('y',at.y-8);path.setAttribute('opacity',String(.42*(1-progress)));},remove(){group.remove();}};
  }
  function random(seed){return()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t^=t+Math.imul(t^t>>>7,61|t);return((t^t>>>14)>>>0)/4294967296;};}
  const messageNames={'meeting.request':'预约会议','safety.checkin':'报平安','repair.request':'预约维修','meal.invitation':'吃饭邀请','travel.update':'行程同步','document.share':'资料交流'};
  return {mount,flow,random,components,subs,messageLabel:name=>messageNames[name]||name};
})();
