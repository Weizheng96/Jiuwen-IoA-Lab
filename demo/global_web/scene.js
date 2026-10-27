/* Fixed editorial choreography; public-run.json supplies measured counts only.
   No server mutation, no fabricated signature, receipt or online status. */
'use strict';
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
let data,timeline,current=-1,shot,origin=performance.now();
const terms=['翻译品牌介绍','写朋友圈文案','给宝宝起名','润色简历','写正则','排杭州两日行程'];
const serviceNames=['中英翻译快译所','朋友圈文案工坊',null,'简历润色工作室','正则片段铺','两日行程定制所'];
const chapters=['平台介绍','个人助手接入','登录赠币','专业服务登记','用户需求与授权','服务发现','合同与履约','支付与信誉','助手间交互','探索智能体经济','对内验证 · 对外体验'];
const C={join:'#8be4d2',credit:'#f4d078',provider:'#9abcfb',needs:'#9be8b9',authorization:'#a2e9c1',discovery:'#b69cf4',contract:'#b9a0ff',delivery:'#8bb9f6',acceptance:'#83e9cd',arbitration:'#f3b0a9',payment:'#f4d078',reputation:'#c0a4ff',messages:'#8dd9d8',wide:'#89d7e4'};
const hide=(el,yes)=>el.style.display=yes?'none':'';
const make=(tag,attrs)=>{let n=document.createElementNS(NS,tag);for(let [k,v]of Object.entries(attrs))n.setAttribute(k,v);return n};
function center(id){const r=$(id).getBoundingClientRect(),m=$('network').getBoundingClientRect(),scale=m.width/1920;return {x:(r.x+r.width/2-m.x)/scale,y:(r.y+r.height/2-m.y)/scale}}
function path(a,b,label,color,p,curve=0,family=''){
 const x=center(a),y=center(b),scale=$('network').getBoundingClientRect().width/1920;
 if(Math.abs(x.x-y.x)>160){let dir=y.x>x.x?1:-1;x.x+=dir*$(a).getBoundingClientRect().width/scale/2;y.x-=dir*$(b).getBoundingClientRect().width/scale/2}
 let mx=(x.x+y.x)/2,my=(x.y+y.y)/2+curve,d=`M${x.x},${x.y} Q${mx},${my} ${y.x},${y.y}`;
 if((family==='credit'||family==='payment')&&(a.startsWith('a')||b.startsWith('a'))){
  let n=Number((a.startsWith('a')?a:b).slice(1)),lane=560+n*10,bottom=680+n*2;
  d=a==='payment'?`M${x.x},${x.y} L970,${x.y} L970,${bottom} L${lane},${bottom} L${lane},${y.y} L${y.x},${y.y}`:`M${x.x},${x.y} L${lane},${x.y} L${lane},${bottom} L970,${bottom} L970,${y.y} L${y.x},${y.y}`;
  mx=550;my=(a==='payment'?y.y:x.y)-5;
 }
 if(family==='acceptance'){d=`M${x.x},${x.y} L1430,${x.y} L1430,42 L550,42 L550,${y.y} L${y.x},${y.y}`;mx=990;my=35}
 const line=make('path',{d,fill:'none',stroke:color,'stroke-width':2,opacity:.65});$('wires').append(line);const point=line.getPointAtLength(line.getTotalLength()*p);$('wires').append(make('circle',{cx:point.x,cy:point.y,r:6,fill:color}));if(label){const t=make('text',{x:mx,y:my-12,fill:color,'font-size':18,'font-weight':700,'text-anchor':'middle','paint-order':'stroke',stroke:'#0a1424','stroke-width':7});t.textContent=label;$('wires').append(t)}
}
function detail(mode){
 let html='';
 if(mode==='methods')html='<h2>服务发现：两种选择</h2><div class="choices"><article><h3>A2X 分类树搜索</h3><div class="tree">智能体服务<br> ├ 软件开发 → 正则 / 代码<br> ├ 翻译本地化 → 中英翻译<br> └ 旅行出行 → 两日行程</div><div class="pill">本轮实际使用 · 分类导航＋重排</div></article><article><h3>文本向量相似度 · ANN</h3><p>需求与服务描述 → 向量<br>近邻索引 → 候选列表</p><div class="vector"></div><div class="vector" style="width:83%"></div><div class="vector" style="width:66%"></div><div class="pill muted">方案说明 · 本轮未启用ANN</div></article></div><p>本轮实测：A2X分类树搜索 '+data.proof.a2xTreeSearches+' 次，运行事件可追踪。</p>';
 if(mode==='compare')html='<h2>A2C：先比较，再协商</h2><table><tr><th>比较维度</th><th>候选服务</th><th>筛选依据</th></tr><tr><td>交付件与入参</td><td>模板公开说明</td><td>是否覆盖用户需求</td></tr><tr><td>要价</td><td>定数报价</td><td>是否在授权预算内</td></tr><tr><td>验收标准</td><td>判据及不覆盖项</td><td>能否客观核验成果</td></tr></table><p class="notice">比较机制示意；具体报价与签字以实际合同为准。</p>';
 if(mode==='contract')html='<h2>委托发生前，明确“交什么、怎么验”</h2><table><tr><td>交付成果</td><td>交付件名称、格式、范围</td></tr><tr><td>验收标准</td><td>验收器引用＋摘要＋不覆盖项</td></tr><tr><td>合同条款</td><td>定数报价、期限、亲签要求</td></tr><tr><td>成约方式</td><td>预批准 / 待批准 / 提案</td></tr></table>';
 if(mode==='arbitration')html='<h2>条件分支：双方发生分歧时</h2><p style="font-size:27px;line-height:2">合同与证据 → 按约复算 → 仲裁文书</p><p>判据来自合同，依据来自签名链与交付证据。</p><p class="notice">机制示意 · 本轮没有裁定；文书不触发强制划款。</p>';
 $('detail').innerHTML=html;$('detail').style.display=html?'block':'none';
}
function enter(index){
 current=index;shot=timeline[index];document.querySelectorAll('.lit,.dim').forEach(n=>n.classList.remove('lit','dim'));
 if(shot.stage<9)document.querySelectorAll('.component').forEach(n=>{if(!(shot.focus||[]).includes(n.id))n.classList.add('dim')});
 for(let key of shot.focus||[])$(key).classList.add('lit');
 $('cover').style.display=shot.mode==='cover'?'flex':'none';$('chapter').textContent=String(shot.stage).padStart(2,'0')+'  '+chapters[shot.stage];$('caption').textContent=shot.text;
 $('closing').style.display=shot.stage>=9?'block':'none';$('network').style.transform=shot.stage>=9?'scale(.56) translate(20px,100px)':'';
 $('badge').textContent=shot.stage>=9?'全景概念动画＋真实监控回放':'概念动画 · 真实运行数据';
 $('callout').style.display='none';
 const labels={authorization:'支付授权机制示意 · 预算上限＋可委托类别',acceptance:'验收器由个人助手本地SDK执行',credit:'登录奖励100虚拟币 · 体验委托与协作',meeting:'按账号查询接口 → inputSchema校验 → 离线投递 → 上线处理'};
 if(labels[shot.mode]){$('callout').textContent=labels[shot.mode];$('callout').style.display='block'}
 detail(shot.mode==='methods'?'':shot.mode);
 if(shot.stage>=9){let title=shot.mode==='internal'?'对内 · 仿真验证':shot.mode==='external'?'对外 · 开放体验':'开放网络 · 多方参与';let list=shot.mode==='internal'?['可用性','效率','可靠性','发现技术需求']:shot.mode==='external'?['开发者接入','低门槛趣味体验','真实行为数据','扩大九问影响力']:['个人助手','专业服务','协议组件','P2P交互'];$('closingTitle').textContent=title;$('closingList').replaceChildren(...list.map(x=>{let n=document.createElement('li');n.textContent=x;return n}))}
}
function paint(now){
 const t=(now-origin)/1000;let index=timeline.findLastIndex(s=>s.at<=t);if(index<0)index=0;if(index!==current)enter(index);
 const dt=t-shot.at,dur=shot.hold,progress=Math.min(1,dt/dur),mode=shot.mode,wide=shot.stage>=9;
 $('wires').replaceChildren();let families=new Set();
 function flow(a,b,label,family,i=0){families.add(family);path(a,b,label,C[family]||'#91e2d2',((dt/(wide?.8:2.4)+i*.18)%1),i%2?-20:15,family)}
 for(let i=0;i<6;i++){
  const a=$('a'+i),s=$('s'+i),u=$('u'+i);let online=shot.stage>=2 || mode==='join'&&progress>(i+.35)/6;
  if(wide)online=Math.floor(dt*1.3+i)%7!==0;
  a.classList.toggle('offline',!online);s.classList.toggle('offline',shot.stage<3||mode==='provider'&&progress<(i+.3)/6);u.style.opacity=shot.stage>=4?'1':'.18';
  const accepting=mode==='acceptance'&&i===Math.floor(dt/.95)%6;
  a.classList.toggle('lit',accepting);s.classList.toggle('lit',accepting);
  a.querySelector('small').textContent=online?(accepting?'本地WASM验收':'九问 Swarm · 已接入'):'九问 Swarm · 未登录';
  a.querySelector('.amount').textContent=shot.stage>=2&&progress>(mode==='credit'?(i+.3)/6:0)?'100':'';
  if(mode==='join'&&online)flow('a'+i,'identity','接入登记','join',i);
  if(mode==='credit'&&progress>(i+.2)/6)flow('payment','a'+i,'＋100','credit',i);
  if(mode==='provider'&&progress>(i+.2)/6)flow('s'+i,i%2?'templates':'registry',i%2?'公示模板':'注册服务','provider',i);
  if(mode==='needs')flow('u'+i,'a'+i,'需求','needs',i);
  if(mode==='authorization')flow('u'+i,'a'+i,'授权','authorization',i);
  if(mode==='discovery')flow('a'+i,'registry','发现','discovery',i);
  if(mode==='delivery')flow('s'+i,'notary','交付存证','delivery',i);
  if(accepting)flow('s'+i,'a'+i,'取件 → 个人助手本地验收','acceptance',i);
  if(mode==='payment')flow(i%2?'payment':'a'+i,i%2?'s'+i:'payment',i%2?'过账':'按约付款','payment',i);
  if(mode==='reputation')flow('s'+i,'reputation','结清记录','reputation',i);
  if(mode==='messages'||mode==='meeting'){flow('a'+i,'messages',mode==='meeting'?'预约会议':'P2P消息','messages',i);flow('messages','a'+((i+1)%6),'投递','messages',i+1)}
  if(wide&&online){const targets=['registry','contracts','notary','payment','messages','reputation'];flow('a'+i,targets[(i+Math.floor(dt*2))%6],'','wide',i);flow(targets[(i+Math.floor(dt*2))%6],'s'+i,'','wide',i+1)}
 }
 if(mode==='methods'){$('cursor').style.display=dt<1.2?'block':'none';$('cursor').style.left='830px';$('cursor').style.top='310px';if(dt>=.8&&$('detail').style.display==='none')$('registry').click()}else $('cursor').style.display='none';
 if(mode==='cover'){$('cover').style.opacity=Math.min(1,.3+dt*1.4);$('cover').style.transform='scale('+(1+.006*Math.min(dt,5))+')'}
 if(wide){let start=timeline.find(s=>s.stage>=9).at;let frame=Math.min(window.monitorFrames-1,Math.floor((t-start)*4*2));$('monitor').src='monitor/'+String(Math.max(0,frame)).padStart(4,'0')+'.jpg'}
 $('progress').style.width=(t/window.totalSeconds*1920)+'px';
 window.frameInfo={stage:shot.stage,mode,families:[...families],subtitle:shot.text,cover:mode==='cover',monitor:wide};
 requestAnimationFrame(paint);
}
Promise.all([fetch('public-run.json').then(r=>r.json()),fetch('timeline.json').then(r=>r.json())]).then(([d,sc])=>{
 data=d;timeline=sc.shots;window.totalSeconds=sc.duration;window.monitorFrames=sc.monitorFrames;
 if(serviceNames.filter(Boolean).some(name=>!d.services.some(s=>s.name===name)))throw Error('The public run is missing a narrated registered service');
 for(let i=0;i<6;i++){
  let y=85+i*111;let actor=document.createElement('article');actor.id='a'+i;actor.className='actor offline';actor.style.top=y+'px';actor.innerHTML='<span class="avatar">九问</span><b>个人助手 '+String(i+1).padStart(2,'0')+'</b><small>九问 Swarm · 未登录</small><span class="amount"></span>';$('actors').append(actor);
  let user=document.createElement('article');user.id='u'+i;user.className='user';user.style.top=y+'px';let title=document.createElement('b');title.textContent=terms[i];let sub=document.createElement('small');sub.textContent='用户需求 · 预算与类别';user.append(title,sub);$('actors').append(user);
  let service=document.createElement('article');service.id='s'+i;service.className='service offline';service.style.top=y+'px';let name=document.createElement('b');let registered=d.services.find(s=>s.name===serviceNames[i]);name.textContent=registered?.name||(serviceNames[i]?'专业服务':'起名服务（示意）');let status=document.createElement('small');status.textContent=registered?'公开模板 · 可接取委托':'接入示例 · 非本轮运行';service.append(name,status);$('services').append(service);
 }
 $('realstats').textContent=d.proof.participants+'位独立客户端 · '+d.proof.orders+'笔委托 · '+d.proof.messages+'条消息';
 $('registry').onclick=()=>detail('methods');origin=performance.now();window.ready=true;requestAnimationFrame(paint);
}).catch(e=>{document.body.textContent='Recording source failed: '+e.message;throw e});
