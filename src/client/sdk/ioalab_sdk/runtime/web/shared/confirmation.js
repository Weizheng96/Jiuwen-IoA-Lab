// Independent user decision channel. No decision is carried through tools/call.
function supportsInlineConfirmation(form){
  return form.kind.startsWith('delegation.')||['messaging.interface','registry.interfaces'].includes(form.kind);
}
const confirmationCard = (() => {
  let entry=null, item=null, generation=0, submitting=false, checking=false, uncertain=false, reconnect=null, reconnecting=false, blocked=false, shownReference=null;
  const statusLabels={pending:'等待您的独立批准',approved:'已确认允许此操作。',rejected:'已拒绝此操作。',expired:'业务确认已过期，不能据此继续批准。刷新或换浏览器不会续期；后续处理遵循原业务流程。',answered:'已提交本环节答复。'};
  const blockedMessage='宿主阻止了当前内嵌连接，可能是客户端重启后地址变化。请重新打开当前聊天中的原表单以加载新界面，或点击“在浏览器确认”；反复刷新状态不能更新宿主连接权限。';
  const node=id=>document.getElementById(id);
  function message(text){node('confirmationStatus').textContent=text;}
  function controls(){
    for(const button of node('confirmationActions').querySelectorAll('button'))button.disabled=submitting||checking||uncertain||item?.status!=='pending';
    for(const input of node('confirmationQuestions').querySelectorAll('select,textarea'))input.disabled=submitting||checking||uncertain||item?.status!=='pending';
    node('confirmationRefresh').hidden=!uncertain||!entry?.token||blocked;
    node('confirmationRefresh').disabled=submitting||checking||reconnecting||!entry?.token||blocked;
    node('confirmationRefresh').textContent=checking?'正在核对…':'核对确认结果';
    node('confirmationReconnect').hidden=!uncertain||blocked||!!item&&item.status!=='pending';
    node('confirmationReconnect').disabled=submitting||reconnecting||!reconnect;
  }
  window.addEventListener('securitypolicyviolation',event=>{
    if(event.effectiveDirective==='connect-src'&&entry&&event.blockedURI.startsWith(entry.origin)){
      blocked=true;uncertain=true;controls();message(blockedMessage);
    }
  });
  function value(v){
    const out=document.createElement('div');
    if(v&&typeof v==='object'){
      for(const [key,child]of Object.entries(v)){const line=document.createElement('div');if(!Array.isArray(v)){const title=document.createElement('strong');title.textContent=key+'：';line.append(title);}line.append(value(child));out.append(line);}
    }else out.textContent=v==null?'未提供':String(v);
    return out;
  }
  function draw(){
    node('confirmationPreview').replaceChildren();node('confirmationQuestions').replaceChildren();
    node('confirmationActions').replaceChildren();
    node('confirmationTitle').textContent=item.view?.title||'确认原操作';
    node('confirmationIdentity').textContent=item.expiresAt?'确认有效至 '+new Date(item.expiresAt).toLocaleString():'请核对本次操作的对象、金额和授权范围。';
    for(const section of item.view?.sections||[]){const row=document.createElement('section'),heading=document.createElement('h3');heading.textContent=section.label;row.append(heading,value(section.value));node('confirmationPreview').append(row);}
    node('confirmationOriginal').textContent=JSON.stringify({agentId:item.agentId,stage:item.stage,preview:item.binding.preview},null,2);
    if(item.kind==='question'&&item.status==='pending')for(const [index,q]of item.questions.entries()){
      const label=document.createElement('label');label.textContent=q.question||q.title||q.text||'合同环节问题';
      if(q.options?.length){const select=document.createElement('select');select.dataset.answer=index;select.multiple=!!q.multi_select;
        const empty=document.createElement('option');empty.value='';empty.textContent='请选择';select.append(empty);
        for(const o of q.options){const option=document.createElement('option');option.value=o.value;option.textContent=o.label||o.text||o.value;select.append(option);}label.append(select);}
      if(q.allow_custom_input!==false){const input=document.createElement('textarea');input.dataset.text=index;input.placeholder='您的答复';label.append(input);}node('confirmationQuestions').append(label);
    }
    if(item.status==='pending')for(const [decision,label]of item.kind==='question'?[['answer','提交本环节答复']]:[['approve','允许此操作'],['reject','拒绝此操作']]){
      const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>decide(decision);node('confirmationActions').append(button);
    }
    message(statusLabels[item.status]||item.status);controls();
  }
  async function request(body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const r=await fetch(entry.origin+'/user/confirmation-app/'+(body?'decision':'status'),{method:body?'POST':'GET',credentials:'omit',referrerPolicy:'no-referrer',signal:controller.signal,headers:{Authorization:'Bearer '+entry.token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
      const result=await r.json();if(!r.ok)throw Object.assign(new Error(result.error?.message||'确认未完成'),{httpStatus:r.status,code:result.error?.code,recovery:result.error?.recovery});return result;
    }finally{clearTimeout(timer);}
  }
  async function refresh(){
    if(submitting||!entry?.token||blocked)return;
    const version=++generation;checking=true;controls();message('正在核对原确认结果…');
    let decided=false;
    try{
      const next=await request();if(version!==generation)return;
      if(next.reference!==entry.reference||next.bindingDigest!==entry.bindingDigest)throw new Error('确认对象已变化');
      // A delayed read must not roll a locally observed final decision back to pending.
      if(item&&item.status!=='pending'&&next.status==='pending')return;
      const first=!item;item=next;uncertain=false;draw();
      decided=['approved','rejected','answered'].includes(item.status);
      if(first)requestAnimationFrame(()=>node('confirmationCard').scrollIntoView({block:'start'}));
    }catch(e){if(version!==generation)return;uncertain=true;controls();message(blocked?blockedMessage:e.recovery==='refresh_confirmation_entry'?'内嵌连接已失效。请点击“重新连接原确认”，只核对原记录，不会续期或重新批准。':'内嵌确认暂不可用。请点击“重新连接原确认”核对连接，或点击“在浏览器确认”。');}
    finally{if(version===generation){checking=false;controls();}}
    if(decided)await refreshBusiness();
  }
  async function refreshBusiness(){
    if(!reconnect)return;
    const reference=shownReference,label=statusLabels[item.status];
    message(label+' 正在查询业务进度；如宿主提示，请允许本次状态查询。');
    let refreshed=false;
    try{refreshed=await reconnect();}catch(e){/* The recorded decision remains valid if the read fails. */}
    if(shownReference===reference&&!node('confirmationCard').hidden)
      message(label+(refreshed?' 已查询业务进度，请查看表单结果；仍在处理中可点击“刷新状态”。':' 业务进度暂未取得，请点击“刷新状态”；无需再次确认。'));
  }
  async function decide(decision){
    if(submitting||checking||uncertain||item?.status!=='pending')return;
    const body={reference:item.reference,bindingDigest:item.bindingDigest,decision};
    if(decision==='answer')body.answers=item.questions.map((q,i)=>({selected_options:[...(node('confirmationQuestions').querySelector(`[data-answer="${i}"]`)?.selectedOptions||[])].map(o=>o.value).filter(Boolean),custom_input:node('confirmationQuestions').querySelector(`[data-text="${i}"]`)?.value||''}));
    submitting=true;++generation;controls();message('正在提交您的决定…');
    let decided=false;
    try{
      const result=await request(body);
      if(result.reference!==body.reference||result.bindingDigest!==body.bindingDigest||result.status==='pending')throw new Error('尚未核实原决定');
      item={...item,...result};draw();decided=true;
    }catch(e){
      if(e.code==='expired'){item={...item,status:'expired'};draw();}
      else if(e.recovery==='refresh_confirmation_entry'){uncertain=true;message('内嵌连接已失效。请点击“重新连接原确认”核对原记录，不会自动重发决定。');}
      else{uncertain=!(e.httpStatus>=400&&e.httpStatus<500);message(uncertain?'提交结果待核实；请点“核对确认结果”，不会自动重发决定。':e.message);}
    }
    finally{submitting=false;controls();}
    if(decided)await refreshBusiness();
  }
  node('confirmationRefresh').onclick=refresh;
  node('confirmationReconnect').onclick=async()=>{
    if(submitting||reconnecting||!reconnect)return;
    reconnecting=true;controls();message('正在重新读取原确认；如宿主提示，请允许本次状态查询。');
    try{if(!await reconnect())message('未能读取原确认，请查看页面错误提示后重试。原决定未重新提交。');}
    finally{reconnecting=false;controls();}
  };
  return {show(form,candidate,reload){
    if(submitting)return;
    reconnect=reload;
    shownReference=form.result?.data?.confirmationRef||null;
    const matches=candidate?.formReference===form.reference&&candidate.revision===form.revision&&candidate.reference===form.result?.data?.confirmationRef;
    const applicable=transport.mode==='app'&&supportsInlineConfirmation(form)&&form.result?.data?.confirmationRef&&(form.phase==='waiting'||matches&&candidate.status);
    node('confirmationCard').hidden=!applicable;if(!applicable){entry=null;item=null;++generation;return;}
    if(matches&&candidate.status&&candidate.status!=='pending'){
      entry=null;item={status:candidate.status};++generation;uncertain=false;
      for(const id of ['confirmationPreview','confirmationQuestions','confirmationActions'])node(id).replaceChildren();
      node('confirmationOriginal').textContent='';node('confirmationTitle').textContent='原确认状态';
      node('confirmationIdentity').textContent=candidate.expiresAt?'原确认有效至 '+new Date(candidate.expiresAt).toLocaleString():'原确认状态已核对';
      message(statusLabels[candidate.status]||candidate.status);controls();return;
    }
    if(!candidate?.token||!matches){
      entry=null;item=null;++generation;uncertain=true;node('confirmationPreview').replaceChildren();node('confirmationQuestions').replaceChildren();node('confirmationActions').replaceChildren();node('confirmationOriginal').textContent='';node('confirmationIdentity').textContent='';message('宿主未提供可用的内嵌确认入口，请点击“重新连接原确认”，或“在浏览器确认”。');controls();return;
    }
    try{const url=new URL(candidate.origin);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw Error();}
    catch(e){entry=null;item=null;++generation;uncertain=true;controls();message('内嵌确认入口无效，请重新读取原表单。');return;}
    if(entry?.token===candidate.token)return;
    entry=candidate;item=null;uncertain=true;controls();message(blocked?blockedMessage:'正在读取原确认内容…');void refresh();
  }};
})();
