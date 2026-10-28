/* Reproducible finite editorial events. Concept only; real states come from capture. */
'use strict';
const $=id=>document.getElementById(id);
const ids={identity:'pnIdentity',registry:'pnRegistry',templates:'pnTemplates',contracts:'pnContracts',notary:'pnNotary',reputation:'pnLedger',arbitration:'pnArbitration',payment:'pnPay',messages:'pnMessages'};
const chapters=['平台介绍','个人助手接入','登录赠币','专业服务登记','用户需求与授权','服务发现','合同与履约','支付与信誉','助手间交互','探索智能体经济','对内验证 · 对外体验'];
const terms=['翻译品牌介绍','写朋友圈文案','给宝宝起名','润色简历','写正则','排杭州两日行程'];
const kinds=['预约会议','报平安','预约维修','吃饭邀请','行程同步','资料交流'];
let data,timeline,current=-1,shot,origin=performance.now(),plan=[],active=new Map(),transitionNodes=[];
const TRANSITION_SECONDS=2.8;
const colors={join:'#89e0d2',credit:'#f3d17a',provider:'#8bb8f2',needs:'#9ce5b9',authorization:'#9ce5b9',discovery:'#baa2f0',delivery:'#8ab3ef',acceptance:'#87dec4',payment:'#f3d17a',reputation:'#c3a9f4',messages:'#8bdedb'};
function detail(mode){
 let html='';
 if(mode==='methods')html='<h2>服务发现：两种选择</h2><div class="choices"><article><h3>A2X 分类树搜索</h3><div class="tree">智能体服务<br> ├ 软件开发 → 正则 / 代码<br> ├ 翻译本地化 → 中英翻译<br> └ 旅行出行 → 两日行程</div><div class="pill">本轮实际使用 · 分类导航＋重排</div></article><article><h3>文本向量相似度 · ANN</h3><p>需求与服务描述 → 向量<br>近邻索引 → 候选列表</p><div class="vector"></div><div class="vector" style="width:83%"></div><div class="vector" style="width:66%"></div><div class="pill muted">方案说明 · 本轮未启用ANN</div></article></div><p>本轮实测：A2X分类树搜索 '+data.proof.a2xTreeSearches+' 次，运行事件可追踪。</p>';
 if(mode==='compare')html='<h2>A2C：先比较，再协商</h2><table><tr><th>比较维度</th><th>候选服务</th><th>筛选依据</th></tr><tr><td>交付件与入参</td><td>模板公开说明</td><td>是否覆盖用户需求</td></tr><tr><td>要价</td><td>定数报价</td><td>是否在授权预算内</td></tr><tr><td>验收标准</td><td>判据及不覆盖项</td><td>能否客观核验成果</td></tr></table><p class="notice">比较机制示意；具体报价与签字以实际合同为准。</p>';
 if(mode==='contract')html='<h2>委托发生前，明确“交什么、怎么验”</h2><table><tr><td>交付成果</td><td>交付件名称、格式、范围</td></tr><tr><td>验收标准</td><td>验收器引用＋摘要＋不覆盖项</td></tr><tr><td>合同条款</td><td>定数报价、期限、亲签要求</td></tr><tr><td>成约方式</td><td>预批准 / 待批准 / 提案</td></tr></table>';
 if(mode==='arbitration')html='<h2>条件分支：双方发生分歧时</h2><p style="font-size:27px;line-height:2">合同与证据 → 按约复算 → 仲裁文书</p><p>判据来自合同，依据来自签名链与交付证据。</p><p class="notice">机制示意 · 本轮没有裁定；文书不触发强制划款。</p>';
 $('detail').innerHTML=html;$('detail').style.display=html?'block':'none';
}

function makePlan(s,index){
 const rng=NetworkMap.random(92731+index*7919),events=[],count=data.actors.length,services=data.services.length;
 const duration=()=>.23+rng()*.27;
 function add(at,a,b,label,family){events.push({id:index+'-'+events.length,at,a,b,label,family,duration:duration()});}
 const randomActor=()=>Math.floor(rng()*count),randomService=()=>Math.floor(rng()*services);
 const times=n=>Array.from({length:n},()=>.15+rng()*Math.max(.1,s.hold-1.2));
 const mode=s.mode;
 if(mode==='join'||mode==='credit'||mode==='provider'){
  const n=mode==='provider'?services:count;
  times(n).forEach((at,i)=>add(at,mode==='credit'?'pnPay':(mode==='provider'?'s':'a')+i,mode==='credit'?'a'+i:mode==='provider'?(rng()<.5?'pnRegistry':'pnTemplates'):'pnIdentity',mode==='credit'?'+100':mode==='join'?'接入':'服务登记',mode));
 }else if(['needs','authorization','discovery','delivery','acceptance','payment','reputation','messages','meeting'].includes(mode)){
  times(Math.max(count,Math.floor(s.hold*7))).forEach((at,i)=>{
   const a='a'+randomActor(),b='s'+randomService();
   if(mode==='needs'||mode==='authorization')add(at,a+'user',a,i%5===0?(mode==='needs'?terms[i%terms.length]:'用户授权'):'',mode);
   if(mode==='discovery')add(at,a,'pnRegistry',i%4===0?'A2X搜索':'','discovery');
   if(mode==='delivery')add(at,b,'pnNotary',i%4===0?'交付存证':'','delivery');
   if(mode==='acceptance')add(at,b,a,i%4===0?'取件 · 本地验收':'','acceptance');
   if(mode==='payment'){add(at,a,'pnPay','按约付款','payment');add(at+.45+rng()*.25,'pnPay',b,'虚拟币过账','payment');}
   if(mode==='reputation')add(at,b,'pnLedger',i%4===0?'履约记录':'','reputation');
   if(mode==='messages'||mode==='meeting'){
    const kind=kinds[Math.floor(rng()*kinds.length)];let target=randomActor();while('a'+target===a)target=randomActor();
    add(at,a,'pnMessages',kind,'messages');
    const receive=at+.6+rng()*1.8;if(receive<s.hold-.45)add(receive,'pnMessages','a'+target,i%3===0?kind+' · 收取':'收取','messages');
   }
  });
 }
 return events.sort((a,b)=>a.at-b.at);
}
function enter(index){
 current=index;shot=timeline[index];active.forEach(x=>x.remove());active.clear();$('pnWires').replaceChildren();
 document.querySelectorAll('.lit,.dim,.component-focus').forEach(n=>n.classList.remove('lit','dim','component-focus'));
 document.querySelectorAll('#pn .pn-infra').forEach(n=>n.classList.toggle('dim',shot.stage<9&&!(shot.focus||[]).some(key=>n.id===ids[key]||n.contains($(ids[key])))));
 $('cover').style.display=shot.mode==='cover'?'flex':'none';$('chapter').textContent=String(shot.stage).padStart(2,'0')+'  '+chapters[shot.stage];$('caption').textContent=shot.text;
 const live=shot.stage>=9;$('network').style.display='block';$('realMonitor').style.display=live?'block':'none';$('badge').textContent='概念动画 · 样例服务 · 虚拟币';
 if(live&&!transitionNodes.length){
  transitionNodes=data.actors.map((actor,i)=>{
   const element=$('a'+i),from=element.getBoundingClientRect(),agent=actor.agents.find(a=>a.role==='assistant');
   const to=data.layout.assistants.find(a=>a.id===agent.id);if(!to)throw Error('Missing real assistant geometry');
   return {element,from:{x:from.x,y:from.y,width:from.width,height:from.height},to};
  });
  [['components','.pn-mid'],['services','.pn-right']].forEach(([key,selector])=>{const element=document.querySelector('#pn>'+selector),from=element.getBoundingClientRect(),to=data.layout.panels[key];transitionNodes.push({element,from:{x:from.x,y:from.y,width:from.width,height:from.height},to});});
 }
 document.body.classList.toggle('monitor-only',live&&shot.mode==='tail');
 $('callout').style.display='none';
 const labels={authorization:'用户授权机制示意 · 预算上限＋可委托类别',acceptance:'个人助手通过本地SDK执行WASM验收',credit:'每次登录赠送100虚拟币',meeting:'多种结构化消息 · 异步发送与收取'};
 if(labels[shot.mode]){$('callout').textContent=labels[shot.mode];$('callout').style.display='block';}
 detail(['methods','compare','contract','arbitration'].includes(shot.mode)&&shot.mode!=='methods'?shot.mode:'');
 plan=makePlan(shot,index);window.eventPlan=plan;
}
function paint(now){
 const t=(now-origin)/1000;let index=timeline.findLastIndex(s=>s.at<=t);if(index<0)index=0;if(index!==current)enter(index);
 const dt=t-shot.at,live=shot.stage>=9;
 document.querySelectorAll('#pn .lit').forEach(n=>n.classList.remove('lit'));
 const families=new Set();
 for(const event of plan){
  const key=event.id,p=(dt-event.at)/event.duration;
  if(p<0||p>=1){if(active.has(key)){active.get(key).remove();active.delete(key);}continue;}
  const a=$(event.a),b=$(event.b);if(!a||!b)continue;
  if(!active.has(key))active.set(key,NetworkMap.flow($('pnWires'),a,b,event.label,colors[event.family]));
  active.get(key).paint(p);a.classList.add('lit');b.classList.add('lit');families.add(event.family);
 }
 for(let i=0;i<data.actors.length;i++){
  const event=plan.find(e=>e.a==='a'+i||e.b==='a'+i),online=shot.stage>=2||shot.mode==='join'&&event&&dt>=event.at;
  $('a'+i).classList.toggle('offline',!online);$('a'+i+'user').style.opacity=shot.stage>=4?'1':'.2';
  $('a'+i).querySelector('.state').textContent=online?'九问 · 概念接入':'未登录';
  $('a'+i).querySelector('.wallet').textContent=shot.stage>=2&&(shot.mode!=='credit'||event&&dt>=event.at+event.duration)?'100':'';
 }
 data.services.forEach((s,i)=>{const event=plan.find(e=>e.a==='s'+i);$('s'+i).classList.toggle('offline',shot.stage<3||shot.mode==='provider'&&(!event||dt<event.at));});
 if(shot.mode==='methods'){
  $('cursor').style.display=dt<1.1?'block':'none';const r=$('pnRegistry').getBoundingClientRect();$('cursor').style.left=r.x+r.width/2+'px';$('cursor').style.top=r.y+r.height/2+'px';if(dt>=.8&&$('detail').style.display==='none')detail('methods');
 }else $('cursor').style.display='none';
 let transitionProgress=0,monitorFrame=0,geometryError=0;
 if(live){
  const start=timeline.find(s=>s.stage>=9).at,elapsed=t-start,frame=Math.min(window.monitorFrames-1,Math.floor(Math.max(0,elapsed-TRANSITION_SECONDS)*window.monitorRate*window.monitorFps));monitorFrame=frame;
  $('monitor').src='monitor/'+String(Math.max(0,frame)).padStart(4,'0')+'.jpg';
  transitionProgress=Math.min(1,Math.max(0,(t-start)/TRANSITION_SECONDS));
  const move=Math.max(0,Math.min(1,(elapsed-.35)/1.25)),smooth=move*move*(3-2*move);
  const blend=Math.max(0,Math.min(1,(elapsed-.9)/1.9)),fade=blend*blend*(3-2*blend);
  document.querySelectorAll('.concept-user').forEach(el=>{el.style.opacity=String(Math.max(0,1-elapsed/.65));el.style.filter='blur('+(Math.min(1,elapsed/.65)*12)+'px)';});
  transitionNodes.forEach(({element,from,to})=>{element.style.transform=`translate(${(to.x-from.x)*smooth}px,${(to.y-from.y)*smooth}px) scale(${1+(to.width/from.width-1)*smooth},${1+(to.height/from.height-1)*smooth})`;element.style.opacity='1';element.style.filter='none';const r=element.getBoundingClientRect();geometryError=Math.max(geometryError,Math.abs(r.x-to.x),Math.abs(r.y-to.y),Math.abs(r.width-to.width),Math.abs(r.height-to.height));});
  // Fade one opaque incoming layer over the aligned outgoing layer: no brightness dip.
  $('network').style.opacity='1';$('network').style.filter='none';$('realMonitor').style.opacity=String(fade);$('network').style.display=transitionProgress>=1?'none':'block';
  document.querySelector('footer').style.width=(1920-400*fade)+'px';
  document.querySelector('header').style.opacity=String(Math.max(0,1-transitionProgress*2));$('chapter').style.opacity=String(Math.max(0,1-transitionProgress*2));
 }
 window.transitionProgress=transitionProgress;
 $('progress').style.width=Math.min(1920,t/window.totalSeconds*1920)+'px';
 window.frameInfo={stage:shot.stage,mode:shot.mode,families:[...families],subtitle:shot.text,cover:shot.mode==='cover',monitor:live,transitionProgress,monitorFrame,geometryError,morphTargets:transitionNodes.length,activeFlows:active.size,plannedEvents:plan.length};
 requestAnimationFrame(paint);
}
Promise.all([fetch('public-run.json').then(r=>r.json()),fetch('timeline.json').then(r=>r.json())]).then(async([d,sc])=>{
 data=d;d.services.sort((a,b)=>String(a.category||'').localeCompare(String(b.category||''))||String(a.name).localeCompare(String(b.name)));timeline=sc.shots;window.totalSeconds=sc.duration;window.monitorFrames=sc.monitorFrames;window.monitorFps=sc.monitorFps;window.monitorRate=sc.monitorRate;
 NetworkMap.mount(document.querySelector('#pn .pn-mid'));
 d.actors.forEach((actor,i)=>{
  const pair=document.createElement('div');pair.className='assistant-pair';pair.dataset.pair=String(i);
  const user=document.createElement('article');user.id='a'+i+'user';user.className='concept-user';user.innerHTML='<b>用户 '+String(i+1).padStart(2,'0')+'</b><small>需求 · 授权</small>';
  const el=document.createElement('article');el.id='a'+i;el.className='pn-asst offline';
  el.innerHTML='<span class="ava">九问</span><div class="name"><b>个人助手 '+String(i+1).padStart(2,'0')+'</b></div><span class="state">未登录</span><span class="wallet"></span>';pair.append(user,el);$('pnAsstList').append(pair);
 });
 const groups=new Map();
 d.services.forEach((service,i)=>{
  const cat=d.categories.find(c=>c.id===service.category),key=service.category||'other';
  if(!groups.has(key)){const box=document.createElement('div');box.className='pn-block';const h=document.createElement('div');h.className='bh';h.textContent=cat?.name||key;const list=document.createElement('div');list.className='bl';box.append(h,list);$('pnBlocks').append(box);groups.set(key,list);}
  const el=document.createElement('article');el.className='pn-svc offline';el.id='s'+i;const name=document.createElement('div');name.className='name';name.textContent=service.name;el.append(name);groups.get(key).append(el);
 });
 $('pnRegStat').textContent='服务 '+d.services.length+' · A2X分类树导航';$('pnIdentityStat').textContent=d.actors.length+'位个人助手 · 概念接入，非在线心跳';
 $('pnRegistry').addEventListener('click',()=>detail('methods'));$('monitor').src='monitor/0000.jpg';await $('monitor').decode();origin=performance.now();window.ready=true;requestAnimationFrame(paint);
}).catch(e=>{document.body.textContent='Recording source failed: '+e.message;throw e;});
