'use strict';
(()=>{
  const $=id=>document.getElementById(id),dialog=$('person-history');
  let users=[],session=null,generation=0;
  const stateNames={pending:'等待处理',processing:'处理中',completed:'已完成',rejected:'已拒绝',failed:'处理失败',expired:'已过期',unknown:'结果待核对',queued:'待送达',delivered:'已送达',open:'进行中',closed:'已结束'};
  const label=value=>stateNames[value]||value||'未知';
  const stamp=value=>value?new Date(value*1000).toLocaleString('zh-CN'):'';
  const content=value=>typeof value==='string'?value:value&&typeof value.text==='string'&&Object.keys(value).length===1?value.text:JSON.stringify(value,null,2);
  function node(tag,text,className){const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;}
  function render(){
    const query=$('people-search').value.trim().toLowerCase();
    const visible=users.filter(user=>user.username!==session?.username&&[user.username,user.displayName,...user.assistantNames].join(' ').toLowerCase().includes(query));
    $('people-list').replaceChildren();$('people-status').textContent=visible.length?`${visible.length} 位用户`:'没有符合条件的用户。';
    for(const user of visible){const card=node('button','','directory-card person-card');card.type='button';
      card.append(node('i','','directory-dot blue'),node('h2',user.assistantNames[0]||user.displayName||user.username),node('small','@'+user.username),node('p',user.assistantNames.length?user.assistantNames.join(' · '):'尚未登记个人助手'),node('span','查看你们的交互 →','person-action'));
      card.addEventListener('click',()=>select(user));$('people-list').append(card);}
  }
  function section(title,items,build){const box=node('section','','history-section');box.append(node('h3',title));if(!items.length)box.append(node('p','暂无记录','history-empty'));for(const item of items)box.append(build(item));return box;}
  function record(title,meta,body){const item=node('details','','history-item');item.append(node('summary',title),node('p',meta));for(const [name,value] of body){item.append(node('h4',name),node('pre',content(value)));}return item;}
  async function select(user){
    if(!session||!session.id){location.assign(WebSession.loginUrl('/assistants#'+encodeURIComponent(user.username)));return;}
    location.hash=encodeURIComponent(user.username);const ticket=++generation;
    $('history-title').textContent=user.assistantNames[0]||user.displayName||user.username;$('history-account').textContent='@'+user.username;
    $('history-records').replaceChildren();$('history-status').textContent='正在读取你们的交互…';if(!dialog.open)dialog.showModal();
    try{const data=await WebSession.api('/api/people/'+encodeURIComponent(user.username)+'/history');if(ticket!==generation)return;
      $('history-status').textContent='';
      $('history-records').append(
        section('通信',data.messages,item=>record(`${item.senderUsername===session.username?'你 → 对方':'对方 → 你'} · ${item.interface||'通信'} · ${label(item.requestState)}`,`${stamp(item.created)} · ${label(item.deliveryState)}`,item.payloadAvailable?[['内容',item.payload],...(item.result?[['结果',item.result]]:[])]:[['内容','原内容已不在保留期内']]))
        ,section('委托意向',data.intents,item=>record(item.providerName+' · '+item.phase,stamp(item.created),[['个人助手',item.clientName],['状态',item.phase]]))
        ,section('合同',data.contracts,item=>record((item.provider_name||'专业服务')+' · '+(item.phase||label(item.state)),stamp(item.created),[['个人助手',item.assistant_name],['合同金额',item.total===null?'待恢复':(item.total+' 虚拟币')],['已读卡付款',item.paid+' 虚拟币'],['状态',item.phase||label(item.state)]]))
      );
    }catch(error){if(ticket!==generation)return;if(error.status===401){location.assign(WebSession.loginUrl('/assistants#'+encodeURIComponent(user.username)));return;}
      $('history-status').textContent='交互历史暂时无法读取。';const retry=node('button','重新读取','history-retry');retry.onclick=()=>select(user);$('history-records').append(retry);}
  }
  $('history-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{generation++;history.replaceState(null,'','/assistants');});
  $('people-search').addEventListener('input',render);
  Promise.all([WebSession.api('/api/public/assistants'),WebSession.ready]).then(([data,value])=>{users=data;session=value;render();
    if(location.hash){let username;try{username=decodeURIComponent(location.hash.slice(1));}catch{return;}const user=users.find(item=>item.username===username);if(user)select(user);}
  }).catch(()=>{$('people-status').textContent='用户资料暂时无法读取，请刷新后重试。';});
})();
