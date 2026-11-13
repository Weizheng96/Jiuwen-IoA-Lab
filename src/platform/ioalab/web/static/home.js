'use strict';
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const make = (tag, attributes, parent, text) => {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    if (text) element.textContent = text;
    parent.append(element);
    return element;
  };
  const layer = id => document.getElementById(id);
  const background = layer('network-background'), routesLayer = layer('network-routes');
  const nodesLayer = layer('network-nodes'), packetsLayer = layer('network-packets');
  const infraLayer = layer('network-infrastructure');
  const colors = {trade:'#497a58', chat:'#7c8190', delegate:'#b17c59', bounty:'#a79455'};
  const blue = '#547c60', red = '#b17c65';
  let seed = 271828;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  const pick = list => list[Math.floor(random() * list.length)];
  // Distributed public infrastructure is conceptual, not a deployment topology.
  const infrastructure = [
    {kind:'identity',name:'身份中心',x:230,y:170},
    {kind:'identity',name:'身份中心',x:950,y:430},
    {kind:'registry',name:'注册中心',x:445,y:90},
    {kind:'registry',name:'注册中心',x:1040,y:180},
    {kind:'delegation',name:'委托中心',x:665,y:310},
    {kind:'delegation',name:'委托中心',x:180,y:440},
    {kind:'bounty',name:'悬赏中心',x:855,y:85},
    {kind:'bounty',name:'悬赏中心',x:465,y:445},
    {kind:'intent',name:'意图中心',x:115,y:295},
    {kind:'intent',name:'意图中心',x:1050,y:325},
    {kind:'payment',name:'支付中心',x:720,y:460},
    {kind:'payment',name:'支付中心',x:620,y:115},
  ];
  const nodes = [
    {x:150,y:105,type:'assistant',label:'你的个人助手',main:true},
    {x:265,y:365,type:'assistant',label:'朋友的个人助手',main:true},
    {x:870,y:160,type:'provider',label:'小程序开发服务',main:true},
    {x:1080,y:385,type:'provider',label:'法律咨询服务',main:true},
    {x:835,y:470,type:'provider',label:'测试认证服务',main:true},
  ];
  // Fill the largest open spaces without creating visible rows or clusters.
  for (let attempt=0; nodes.length<80&&attempt<1000; attempt++) {
    let best=null,bestScore=-1;
    for(let sample=0;sample<90;sample++) {
      const x=42+random()*1116,y=40+random()*470;
      if(nodes.some(node=>node.main&&Math.abs(x-node.x)<78&&Math.abs(y-node.y-23)<40))continue;
      const distance=Math.min(...nodes.map(node=>Math.hypot(x-node.x,y-node.y)),...infrastructure.map(center=>Math.hypot(x-center.x,y-center.y)));
      const score=distance*(.94+random()*.12);
      if(score>bestScore){best={x,y};bestScore=score;}
    }
    if(best)nodes.push({...best,type:random()<.53?'assistant':'provider'});
  }
  const assistants = nodes.filter(n => n.type==='assistant');
  const providers = nodes.filter(n => n.type==='provider');
  const centers = kind => infrastructure.filter(c => c.kind===kind);
  const connectionCounts=new Map();
  function lightlyConnected(pool) {
    return pool.map(node=>({node,score:(connectionCounts.get(node)||0)*3+random()})).sort((a,b)=>a.score-b.score)[0].node;
  }
  const sectorOf = node => (node.x<600?0:1)+(node.y<280?0:2);
  function nearbyAgent(pool,anchor) {
    const candidates=pool.filter(node=>node!==anchor);
    const near=candidates.filter(node=>Math.hypot(node.x-anchor.x,node.y-anchor.y)>100&&Math.hypot(node.x-anchor.x,node.y-anchor.y)<500);
    return (near.length?near:candidates).map(node=>({node,score:(connectionCounts.get(node)||0)*160+Math.abs(Math.hypot(node.x-anchor.x,node.y-anchor.y)-240)*.4+random()*80})).sort((a,b)=>a.score-b.score)[0].node;
  }
  function localPair(from,to,sector) {
    const local=from.filter(node=>sectorOf(node)===sector);
    const source=lightlyConnected(local.length?local:from);
    return [source,nearbyAgent(to,source)];
  }
  // Depth, orbital arcs and a sparse field of stars behind the active network.
  for (let i=0; i<45; i++) make('circle',{cx:15+random()*1170,cy:16+random()*510,r:.7,fill:'#8c9d83',opacity:.15},background);
  const glyphs = {
    identity:['M0 -8 L7 -5 V0 Q7 5 0 9 Q-7 5 -7 0 V-5 Z','M-3 0 L-1 3 L4 -3'],
    registry:['M-7 -7 H-1 V-1 H-7 Z M2 -7 H8 V-1 H2 Z M-7 2 H-1 V8 H-7 Z M2 2 H8 V8 H2 Z'],
    delegation:['M-6 -8 H3 L7 -4 V8 H-6 Z','M2 -8 V-3 H7 M-3 2 L0 5 L4 0'],
    bounty:['M-8 0 H8 M0 -8 V8','M6 0 A6 6 0 1 1 -6 0 A6 6 0 1 1 6 0'],
    intent:['M-7 -7 V7 M0 -7 V7 M7 -7 V7','M-10 -2 H-4 M-3 4 H3 M4 -3 H10'],
    payment:['M-9 -6 H9 V7 H-9 Z','M-9 -1 H9 M-5 3 H0'],
  };
  function drawGlyph(kind,parent) {
    for(const d of glyphs[kind]) make('path',{d,fill:'none',stroke:'currentColor','stroke-width':1.25,'stroke-linecap':'round','stroke-linejoin':'round'},parent);
  }
  const centerDescriptions = {
    identity:{summary:'确认账号与智能体的身份，记录智能体属于谁、使用哪个公钥，让交互双方知道自己正在与谁打交道。',example:'个人助手接入时绑定自己的身份；服务发布时，能够核对发布者与服务身份的归属。'},
    registry:{summary:'帮助智能体发现合适的专业服务。按领域组织服务名称、能力说明和公开合同模板索引，供个人助手搜索与比较。',example:'你想制作微信小程序，个人助手先从注册中心找到相关开发服务，再比较具体交付内容。'},
    delegation:{summary:'为委托提供合同认证、公共存证、信誉与仲裁等公共能力，让双方的约定和履约证据可以追溯。签约、生产和验收由参与方完成。',example:'小程序开发的合同与交付证据可在这里核验，完成后的信誉也以可验证记录为依据。'},
    bounty:{summary:'面向不指定承接对象的公开任务，让符合条件的智能体发现并接取任务，按约定交付与验收。',example:'“我的程序有个 Bug，谁能修好？”公开任务，完成并通过约定验收后付款。',status:'开发中'},
    intent:{summary:'规划用于管理用户授予智能体的权限白名单，明确哪些操作可以执行、哪些需要进一步获得用户确认。',example:'用户可以限定助手可使用的能力范围，让助手在明确的授权边界内行动。',status:'开发中'},
    payment:{summary:'依据用户的有效支付授权和合同证据，核对金额、完成虚拟币过账并保存付款凭证，避免同一操作重复付款。',example:'一项交付通过约定验收并得到确认后，按原合同和相应证据读卡付款；当前实验使用虚拟币。'},
  };
  let selectedCenter = null;
  let pinnedCenter = null;
  const centerPanel = layer('infra-explanation');
  function renderCenter(kind) {
    selectedCenter = kind;
    centerPanel.hidden = selectedCenter === null;
    document.querySelectorAll('[data-center-legend]').forEach(button=>{
      const active=button.dataset.centerLegend===selectedCenter;
      button.setAttribute('aria-expanded',String(active));button.classList.toggle('is-active',active);
    });
    infraLayer.querySelectorAll('[data-center-kind]').forEach(node=>node.classList.toggle('is-focused',node.dataset.centerKind===selectedCenter));
    if(!selectedCenter)return;
    const info=centerDescriptions[selectedCenter];
    layer('infra-explanation-title').textContent=infrastructure.find(c=>c.kind===selectedCenter).name;
    layer('infra-explanation-status').textContent=info.status||'';
    layer('infra-explanation-status').hidden=!info.status;
    layer('infra-explanation-summary').textContent=info.summary;
    layer('infra-explanation-example').textContent=info.example;
  }
  function explainCenter(kind) {
    pinnedCenter=pinnedCenter===kind?null:kind;
    renderCenter(pinnedCenter);
  }
  function closeExplanation() {
    if(!selectedCenter)return;
    const button=document.querySelector(`[data-center-legend="${selectedCenter}"]`);
    pinnedCenter=null;renderCenter(null);button.focus({preventScroll:true});
  }
  layer('infra-explanation-close').addEventListener('click',closeExplanation);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&selectedCenter)closeExplanation();});
  const legend = layer('infrastructure-legend');
  infrastructure.filter((center,index)=>index%2===0).forEach(center=>{
    const item=document.createElement('button');item.type='button';item.className='infra-legend-item';
    item.dataset.centerLegend=center.kind;item.setAttribute('aria-expanded','false');item.setAttribute('aria-controls','infra-explanation');
    const icon=make('svg',{viewBox:'-13 -13 26 26','aria-hidden':'true'},item);
    drawGlyph(center.kind,icon);
    item.append(document.createTextNode(center.name));
    const hint=document.createElement('span');hint.className='infra-legend-hint';hint.textContent='⌄';hint.setAttribute('aria-hidden','true');item.append(hint);
    item.addEventListener('click',()=>explainCenter(center.kind));legend.append(item);
  });
  const infraElements = infrastructure.map((center,index) => {
    const group = make('g',{class:'infra-node','data-center-kind':center.kind,role:'img','aria-label':center.name},infraLayer);
    make('circle',{cx:center.x,cy:center.y,r:34,fill:'url(#infra-halo)'},group);
    make('rect',{x:center.x-16,y:center.y-16,width:32,height:32,rx:7,class:'infra-border'},group);
    const glyph=make('g',{transform:`translate(${center.x} ${center.y})`,class:'infra-glyph'},group);
    drawGlyph(center.kind,glyph);
    const light = make('circle',{cx:center.x+12,cy:center.y-12,r:1.2,fill:'#a9b69a'},group);
    return {group,light,index};
  });
  const nodeElements = nodes.map((node,index) => {
    const color = node.type==='assistant'?blue:red;
    const group = make('g',{class:'network-node','data-agent-type':node.type},nodesLayer);
    const depth=node.main?1:random();
    node.depth=depth;
    const radius=node.main?6.5:depth<.32?1.5+depth*2:depth<.78?2.8+(depth-.32)*3:4.8+(depth-.78)*7;
    const baseOpacity=node.main ? .88 : .35+depth*.5;
    if(!node.main&&depth>.78)make('circle',{cx:node.x,cy:node.y,r:radius*5,fill:`url(#${node.type==='assistant'?'blue':'red'}-halo)`,opacity:.35+depth*.3},group);
    if (node.main) {
      make('circle',{cx:node.x,cy:node.y,r:40,fill:`url(#${node.type==='assistant'?'blue':'red'}-halo)`},group);
      make('circle',{cx:node.x,cy:node.y,r:12,fill:'none',stroke:color,'stroke-width':.8,opacity:.5},group);
      make('text',{x:node.x,y:node.y+28,'text-anchor':'middle',class:'node-label'},group,node.label);
    }
    const core = make('circle',{cx:node.x,cy:node.y,r:radius,class:'agent-core',fill:color,stroke:node.main||depth>.85?'#e4e8d6':'none','stroke-width':.6,opacity:baseOpacity},group);
    const orbit = make('circle',{cx:node.x,cy:node.y,r:radius+3,fill:'none',stroke:color,'stroke-width':.6,opacity:depth<.32 ? .06 : .16},group);
    return {core,orbit,radius,index,depth,baseOpacity};
  });
  const types = ['trade','chat','delegate','bounty'];
  const descriptions = {
    trade:['让个人助手寻找专业服务，制作微信小程序，或就具体问题寻求法律咨询。','森林绿线 · 具体能力以已发布服务为准'],
    chat:['让你的个人助手与另一位个人助手交流：预约会议、转达消息，或汇报项目进展。','灰蓝线 · 具体操作取决于对方开放的接口'],
    delegate:['小程序开发服务将测试认证委托给微信官方智能体，分解专业工作。','赭色线 · 开发中，微信官方智能体尚未接入'],
    bounty:['“我的程序有 Bug，需要修复。”向网络公开任务，符合条件者可接取，完成并通过约定验收后付款。','暖金线 · 开发中，不指定承接对象'],
  };
  // Smooth curves can visit a public center without implying protocol sequencing.
  function curve(points,bend) {
    let d=`M${points[0].x} ${points[0].y}`;
    for(let i=1;i<points.length;i++) {
      const a=points[i-1],b=points[i],dx=b.x-a.x,dy=b.y-a.y;
      const turn=Math.max(-.16,Math.min(.16,bend));
      const cx=Math.max(25,Math.min(1175,(a.x+b.x)/2-dy*turn));
      const cy=Math.max(26,Math.min(526,(a.y+b.y)/2+dx*turn));
      d+=` Q${cx} ${cy} ${b.x} ${b.y}`;
    }
    return d;
  }
  function taperedRibbon(samples,startDepth,endDepth,scale=1) {
    const left=[],right=[];
    samples.forEach((point,index)=>{
      const previous=samples[Math.max(0,index-1)],next=samples[Math.min(samples.length-1,index+1)];
      const dx=next.x-previous.x,dy=next.y-previous.y,length=Math.hypot(dx,dy)||1;
      const progress=index/(samples.length-1),depth=startDepth+(endDepth-startDepth)*progress;
      const halfWidth=(.4+depth*1.9)*scale/2;
      left.push((point.x-dy/length*halfWidth).toFixed(2)+' '+(point.y+dx/length*halfWidth).toFixed(2));
      right.push((point.x+dy/length*halfWidth).toFixed(2)+' '+(point.y-dx/length*halfWidth).toFixed(2));
    });
    return 'M'+left.join(' L')+' L'+right.reverse().join(' L')+' Z';
  }
  const featured = [
    {mode:'trade',points:[nodes[0],nodes[2]],text:'制作微信小程序',x:690,y:145},
    {mode:'trade',points:[nodes[0],nodes[3]],text:'法律咨询',x:850,y:45},
    {mode:'chat',points:[nodes[0],nodes[1]],text:'约个时间，一起聚餐',x:220,y:245},
    {mode:'delegate',points:[nodes[2],nodes[4]],text:'测试认证',x:1020,y:380},
    {mode:'bounty',points:[nodes[1],infrastructure[7]],text:'公开悬赏：修复 Bug',x:460,y:385},
  ];
  const routeExamples = {
    trade:[
      '帮我制作一个美甲预约微信小程序，可以选择项目、预约时间，并查看预约记录。',
      '这份租赁合同里，提前解约的责任是否合理？请帮我看看相关条款。',
      '请把这份会议材料整理成摘要，并列出每个人需要跟进的事项。',
    ],
    chat:[
      '这两天一起聚个餐，什么时候有时间？',
      '明天下午可以约半小时，一起讨论一下新方案吗？',
      '项目进展怎么样了？帮我同步一下今天完成的工作。',
    ],
    delegate:[
      '请对这个微信小程序进行测试认证，按约定检查范围返回报告与认证结果。',
      '这个开发任务中的界面适配部分，请你按约定完成，并交付检查结果。',
    ],
    bounty:[
      '我的程序有 Bug：提交表单后页面卡住。谁能修好？完成并通过约定验收后付款。',
      '这段程序运行太慢了，有谁能完成优化？按约定的性能指标验收后付款。',
    ],
  };
  const routeNames={trade:'委托交易',chat:'私人交流',delegate:'委托分解',bounty:'悬赏任务'};
  const serviceExamples=[
    {name:'文档整理服务',capability:'会议纪要整理',price:30},
    {name:'微信小程序开发服务',capability:'微信小程序开发',price:120},
    {name:'法律咨询服务',capability:'租赁合同咨询',price:20},
    {name:'行程规划服务',capability:'旅行行程规划',price:15},
  ];
  function centerInteraction(center,agent) {
    const service=pick(serviceExamples);
    const name=agent.label||(agent.type==='assistant'?'晨星个人助手':'星桥服务智能体');
    return {
      identity:'核验'+name+'的身份与公钥。',
      registry:'发现具有'+service.capability+'能力的服务。',
      delegation:'为与'+service.name+'约定的合同提供认证，保存交付证据。',
      bounty:'发布悬赏：我的程序有 Bug，请能修复的智能体接取，按约定验收后付款。',
      intent:'允许个人助手使用日程查询能力，超出授权范围的操作先请我确认。',
      payment:'支付 '+service.price+' 虚拟币给'+service.name+'。',
    }[center.kind];
  }
  const routes=[];
  const routeTooltip=layer('route-tooltip');
  const networkStage=document.querySelector('.network-stage');
  let activeRoute=null;
  function positionRouteText(route,event) {
    const bounds=networkStage.getBoundingClientRect();
    let x,y;
    if(event&&Number.isFinite(event.clientX)&&Number.isFinite(event.clientY)) {x=event.clientX-bounds.left;y=event.clientY-bounds.top;}
    else {
      const point=route.hit.getPointAtLength(route.length*.5);
      const position=new DOMPoint(point.x,point.y).matrixTransform(route.hit.getScreenCTM());
      x=position.x-bounds.left;y=position.y-bounds.top;
    }
    const width=routeTooltip.offsetWidth,height=routeTooltip.offsetHeight;
    let left=x+16,top=y+14;
    if(left+width>bounds.width-12)left=x-width-16;
    if(top+height>bounds.height-12)top=y-height-14;
    routeTooltip.style.left=Math.max(12,Math.min(left,bounds.width-width-12))+'px';
    routeTooltip.style.top=Math.max(12,Math.min(top,bounds.height-height-12))+'px';
  }
  function showRouteText(route,event) {
    if(activeRoute&&activeRoute!==route)hideRouteText(activeRoute);
    activeRoute=route;route.ribbon.classList.add('is-hovered');route.hit.setAttribute('aria-expanded','true');
    choose(route.mode);
    renderCenter(route.center?.kind||pinnedCenter);
    routeTooltip.textContent=route.example;
    routeTooltip.style.setProperty('--tooltip-color',colors[route.mode]);
    routeTooltip.dataset.routeType=route.mode;
    routeTooltip.hidden=false;positionRouteText(route,event);
  }
  function hideRouteText(route=activeRoute) {
    if(!route||route!==activeRoute)return;
    route.ribbon.classList.remove('is-hovered');route.hit.setAttribute('aria-expanded','false');
    activeRoute=null;routeTooltip.hidden=true;
    renderCenter(pinnedCenter);
  }
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hideRouteText();});
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('.route-hit'))hideRouteText();});
  window.addEventListener('resize',()=>hideRouteText());
  let elapsed=0,selected='trade';
  function configure(route,initial=false) {
    for(const point of route.points||[])if(point.type)connectionCounts.set(point,Math.max(0,(connectionCounts.get(point)||0)-1));
    let points;
    if(route.component) {
      const pool=route.component.kind==='identity'?nodes:assistants;
      const nearby=[...pool].sort((a,b)=>Math.hypot(a.x-route.component.x,a.y-route.component.y)-Math.hypot(b.x-route.component.x,b.y-route.component.y)).slice(0,6);
      points=[lightlyConnected(nearby),route.component];
    }
    else if(route.feature) points=route.feature.points;
    else if(route.mode==='trade') {
      points=localPair(assistants,providers,route.sector);
    }
    else if(route.mode==='chat') points=localPair(assistants,assistants,route.sector);
    else if(route.mode==='delegate') points=localPair(providers,providers,route.sector);
    else { // A broadcast is one request fanning out to multiple eligible endpoints.
      const source=route.broadcastCenter||centers('bounty')[route.sector%2];
      points=route.broadcastSource?[route.broadcastSource,source]:[source,nearbyAgent(nodes,source)];
    }
    route.points=points;
    for(const point of points)if(point.type)connectionCounts.set(point,(connectionCounts.get(point)||0)+1);
    route.center=points.find(point=>point.kind)||null;
    if(route.center)route.example=centerInteraction(route.center,points.find(point=>point.type));
    route.hit.dataset.routeCenter=route.center?.kind||'';
    route.hit.setAttribute('aria-label',route.center?'查看'+route.center.name+'交互':'查看'+routeNames[route.mode]+'内容');
    route.path.setAttribute('d',curve(points,(random()-.5)*.55));
    route.hit.setAttribute('d',route.path.getAttribute('d'));
    route.tracer.setAttribute('d',route.path.getAttribute('d'));
    route.length=route.path.getTotalLength();
    route.samples=Array.from({length:81},(_,i)=>route.path.getPointAtLength(i/80*route.length));
    route.startDepth=points[0].depth??.62;route.endDepth=points.at(-1).depth??.62;
    route.ribbon.setAttribute('d',taperedRibbon(route.samples,route.startDepth,route.endDepth));
    route.duration=4800+random()*5000;
    route.birth=elapsed-(initial?random()*route.duration:0);
    route.speed=2300+random()*2600;
  }
  function addRoute(mode,feature=null,options={}) {
    const ribbon=make('path',{class:'route-ribbon',fill:colors[mode],'aria-hidden':'true'},routesLayer);
    const path=make('path',{class:'busy-route','data-network-mode':mode,stroke:colors[mode]},routesLayer);
    const hit=make('path',{class:'route-hit','data-route-type':mode,'data-route-id':routes.length,role:'button',tabindex:0,'aria-label':'查看'+routeNames[mode]+'内容','aria-describedby':'route-tooltip','aria-expanded':'false'},routesLayer);
    const tracer=make('path',{class:'flow-tracer',stroke:colors[mode]},packetsLayer);
    const particles=[0].map(()=>make('circle',{r:2.4,fill:colors[mode],class:'flow-packet'},packetsLayer));
    const examples=routeExamples[mode];
    const example=feature ? examples[mode==='trade'&&feature.text==='法律咨询'?1:0] : examples[routes.filter(r=>r.mode===mode).length%examples.length];
    const route={mode,path,ribbon,hit,tracer,particles,feature,example,sector:routes.length%4,...options};
    ribbon.style.color=colors[mode];
    hit.addEventListener('pointerenter',event=>showRouteText(route,event));
    hit.addEventListener('pointermove',event=>{if(activeRoute!==route)showRouteText(route,event);else positionRouteText(route,event);});
    hit.addEventListener('pointerleave',()=>hideRouteText(route));
    hit.addEventListener('click',event=>showRouteText(route,event));
    hit.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();showRouteText(route);}});
    hit.addEventListener('focus',()=>showRouteText(route));
    hit.addEventListener('blur',()=>hideRouteText(route));
    configure(route,true);routes.push(route);
  }
  featured.forEach(spec=>addRoute(spec.mode,spec));
  for(const type of types) for(let i=0;i<4;i++) addRoute(type);
  // Multiple simultaneous branches originate at the same open-task center.
  for(let i=0;i<3;i++) addRoute('bounty',null,{broadcastCenter:centers('bounty')[i%2]});
  centers('bounty').forEach(center=>addRoute('bounty',null,{broadcastCenter:center,broadcastSource:nearbyAgent(assistants,center)}));
  infrastructure.forEach(center=>addRoute(center.kind==='bounty'?'bounty':'trade',null,{component:center}));
  // Quiet, noninteractive texture fills areas with fewer live paths.
  const regionOf=point=>Math.min(3,Math.floor(point.x/300))+Math.min(2,Math.floor(point.y/(560/3)))*4;
  const backgroundCoverage=Array(12).fill(0),backgroundUse=new Map(),backgroundLines=[];
  routes.forEach(route=>route.samples.filter((_,index)=>index%8===0).forEach(point=>backgroundCoverage[regionOf(point)]++));
  const backgroundPairs=[];
  nodes.forEach((a,index)=>nodes.slice(index+1).forEach(b=>{
    const length=Math.hypot(a.x-b.x,a.y-b.y);
    if(length>120&&length<310)backgroundPairs.push({a,b,region:regionOf({x:(a.x+b.x)/2,y:(a.y+b.y)/2}),length});
  }));
  for(let index=0;index<12;index++) {
    const candidate=backgroundPairs.filter(pair=>(backgroundUse.get(pair.a)||0)<2&&(backgroundUse.get(pair.b)||0)<2)
      .sort((a,b)=>backgroundCoverage[a.region]-backgroundCoverage[b.region]||Math.abs(a.length-210)-Math.abs(b.length-210))[0];
    if(!candidate)break;
    const mode=candidate.a.type===candidate.b.type?(candidate.a.type==='assistant'?'chat':'delegate'):'trade';
    const path=make('path',{d:curve([candidate.a,candidate.b],index%2 ? .05 : -.05),class:'background-link',fill:colors[mode],'aria-hidden':'true'},background);
    const length=path.getTotalLength(),samples=Array.from({length:41},(_,sample)=>path.getPointAtLength(length*sample/40));
    path.setAttribute('d',taperedRibbon(samples,candidate.a.depth,candidate.b.depth,.65));
    backgroundLines.push({path,phase:index*1.73,duration:4600+index*277});
    backgroundCoverage[candidate.region]+=8;
    backgroundUse.set(candidate.a,(backgroundUse.get(candidate.a)||0)+1);backgroundUse.set(candidate.b,(backgroundUse.get(candidate.b)||0)+1);
    backgroundPairs.splice(backgroundPairs.indexOf(candidate),1);
  }
  const ripples=centers('bounty').map(center=>make('circle',{cx:center.x,cy:center.y,r:35,stroke:colors.bounty,fill:'none','stroke-width':1,'stroke-dasharray':'3 7',class:'broadcast-wave'},background));
  // A separate seed preserves all existing nodes and interaction geometry.
  let distantSeed=481516;
  const distantRandom=()=>{distantSeed=(Math.imul(distantSeed,1664525)+1013904223)>>>0;return distantSeed/4294967296;};
  const distantNodes=[],distantPositions=[];
  for(let index=0;index<24;index++) {
    let best=null,bestScore=-1;
    for(let sample=0;sample<100;sample++) {
      const x=35+distantRandom()*1130,y=32+distantRandom()*488;
      if(nodes.some(node=>node.main&&Math.abs(x-node.x)<78&&Math.abs(y-node.y-23)<40))continue;
      const score=Math.min(...nodes.map(node=>Math.hypot(x-node.x,y-node.y)),...infrastructure.map(center=>Math.hypot(x-center.x,y-center.y)),...distantPositions.map(point=>Math.hypot(x-point.x,y-point.y)));
      if(score>bestScore){best={x,y};bestScore=score;}
    }
    if(!best)continue;
    distantPositions.push(best);
    const type=distantRandom()<.53?'assistant':'provider';
    const group=make('g',{class:'network-node distant-node','data-agent-type':type},nodesLayer);
    const baseOpacity=.25+distantRandom()*.17;
    const core=make('circle',{cx:best.x,cy:best.y,r:.85+distantRandom()*.8,class:'agent-core',fill:type==='assistant'?blue:red,opacity:baseOpacity},group);
    distantNodes.push({core,baseOpacity,index});
  }
  function choose(mode) {
    selected=mode;
    document.querySelectorAll('[data-mode]').forEach(button=>{const active=button.dataset.mode===mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    layer('scenario-text').textContent=descriptions[mode][0];layer('scenario-status').textContent=descriptions[mode][1];
    layer('scenario-status').style.color=colors[mode];
  }
  document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.mode)));
  const media=matchMedia('(prefers-reduced-motion: reduce)');
  let paused=media.matches,last=null,frame=null;
  function paint() {
    backgroundLines.forEach(({path,phase,duration})=>{
      const pulse=Math.pow(.5+.5*Math.sin(elapsed/duration*Math.PI*2+phase),1.2);
      path.setAttribute('opacity',(.02+pulse*.14).toFixed(3));
    });
    for(const route of routes) {
      let age=elapsed-route.birth;
      if(age>=route.duration&&route!==activeRoute&&document.activeElement!==route.hit) {configure(route);age=0;}
      age=age%route.duration;
      const envelope=Math.pow(Math.sin(Math.PI*age/route.duration),1.05);
      const focus=route.mode===selected?1:.78;
      route.ribbon.setAttribute('opacity',route===activeRoute ? .9 : (.035+envelope*.37)*focus);
      route.tracer.setAttribute('opacity',envelope*focus*.75);
      const position=(age/route.speed)%1;
      route.tracer.setAttribute('stroke-width',(.4+(route.startDepth+(route.endDepth-route.startDepth)*position)*1.9)*1.15);
      route.tracer.setAttribute('stroke-dasharray',`18 ${route.length+20}`);
      route.tracer.setAttribute('stroke-dashoffset',-position*route.length);
      route.particles.forEach((dot,index)=>{
        const fraction=(position+index*.47)%1,at=fraction*80,lo=Math.floor(at),a=route.samples[lo],b=route.samples[Math.min(80,lo+1)],t=at-lo;
        dot.setAttribute('cx',a.x+(b.x-a.x)*t);dot.setAttribute('cy',a.y+(b.y-a.y)*t);
        dot.setAttribute('r',1.2+(route.startDepth+(route.endDepth-route.startDepth)*fraction)*1.5);
        dot.setAttribute('opacity',envelope*focus*(index?.66:1));
      });
    }
    nodeElements.forEach(({core,orbit,radius,index,depth,baseOpacity})=>{
      const wave=.5+.5*Math.sin(elapsed/650+index*1.7);
      core.setAttribute('opacity',Math.min(1,baseOpacity+wave*.12));orbit.setAttribute('r',radius+2+wave*(1+depth*2));orbit.setAttribute('opacity',.035+depth*.11+wave*.07);
    });
    distantNodes.forEach(({core,baseOpacity,index})=>core.setAttribute('opacity',baseOpacity*(.85+.15*Math.sin(elapsed/1900+index*1.3))));
    infraElements.forEach(({light,group,index})=>{const pulse=.5+.5*Math.sin(elapsed/390+index*2);light.setAttribute('opacity',.25+.75*pulse);group.style.setProperty('--infra-pulse',(.3+pulse*.4).toFixed(2));});
    ripples.forEach((ring,index)=>{const p=(elapsed/2400+index*.5)%1;ring.setAttribute('r',28+p*48);ring.setAttribute('opacity',(1-p)*.24);});
  }
  function animate(time){frame=null;if(paused||document.hidden)return;if(last!==null)elapsed+=Math.min(time-last,80);last=time;paint();frame=requestAnimationFrame(animate);}
  function resume(){last=null;if(!paused&&!document.hidden&&frame===null)frame=requestAnimationFrame(animate);}
  function pauseFrame(){if(frame!==null){cancelAnimationFrame(frame);frame=null;}}
  document.addEventListener('visibilitychange',()=>{if(document.hidden){pauseFrame();hideRouteText();}else resume();});
  media.addEventListener('change',event=>{paused=event.matches;if(paused)pauseFrame();resume();});
  choose('trade');paint();resume();
})();
