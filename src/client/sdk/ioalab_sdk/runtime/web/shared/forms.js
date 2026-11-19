let current=null, editors={},dirty=false,busy=false,needsRefresh=false,polling=false,revisionConflict=false,editing=false;
const $=id=>document.getElementById(id);
function saveNotice(text){const node=$('saveStatus');node.textContent=text;node.hidden=!text;}
const phases={draft:'草稿',previewed:'已预览',waiting:'等待确认',completed:'已完成',rejected:'已拒绝',failed:'失败',unknown:'待核实原结果',accepted:'已受理',running:'处理中'};
function problem(error){
  $('error').textContent=error.message||String(error);$('error').hidden=false;
  const match=/Check ([^ :]+):/.exec(error.message||'');
  const name=error.field||match?.[1]?.split('.')[0];
  const node=name&&document.getElementById('field-'+name);
  if(node){editing=true;$('fields').hidden=false;for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
    $('error').textContent='请检查“'+(current?.schema.properties?.[name]?.title||name)+'”：'+(match?'填写不完整或格式不符合要求。':error.message);
    (node.matches('input,textarea,select')?node:node.querySelector('input,textarea,select'))?.focus();}
}
function equalValues(a,b){
  const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
  return JSON.stringify(ordered(a))===JSON.stringify(ordered(b));
}
function layout(){
  if(!current)return;
  const reviewing=!!current.preview&&!editing&&!dirty;
  $('fields').hidden=reviewing||current.readOnly;
  $('edit').hidden=!reviewing||current.readOnly;
  $('edit').disabled=busy||needsRefresh;
  $('previewSection').hidden=!current.preview||editing||dirty;
  const direct=directAction(selectedAction());
  $('save').hidden=!!direct||reviewing||current.readOnly;
  $('previewButton').hidden=!!direct||reviewing||current.readOnly;
  $('submit').hidden=!!direct||!reviewing||['completed','rejected','failed','accepted','running','waiting'].includes(current.phase);
  $('stageHint').textContent=current.readOnly?(current.phase==='completed'?'本次操作已完成，可查看原结果。':'当前操作已提交，请查看状态或完成用户确认。'):reviewing?'请核对下方摘要；需要调整时点击“返回修改”。':'已提供的详细内容可展开修改；请补充缺项并核对权限。';
  document.querySelectorAll('.steps li').forEach((node,i)=>node.setAttribute('aria-current',i===(current.readOnly?2:reviewing?1:0)?'step':'false'));
  const selected=current.schema.properties?.action;
  const index=selected?.enum?.indexOf(current.values?.action);
  $('submit').textContent=current.readOnly?'核对并继续原操作':index>=0?'提交：'+(selected.enumNames?.[index]||current.values.action):'提交并申请确认';
  if(current.phase==='waiting'&&current.result?.data?.confirmationRef){$('previewSection').hidden=transport.mode==='app'&&supportsInlineConfirmation(current);}
}

function directAction(action,form=current){
  if(!form||form.readOnly||!form.schema.properties?.action?.enum?.includes(action))return null;
  const label=form.presentation?.directActions?.[action];
  return typeof label==='string'&&label?label:null;
}
function selectedAction(){return editors.action?.read();}
function buttons(){
  $('setupButton').disabled=busy;
  for(const id of ['save','previewButton','submit','run','refresh','browser','discard','recover'])$(id).disabled=busy||!current;
  $('discard').hidden=!dirty;
  $('refresh').textContent=revisionConflict?'加载最新版本':!needsRefresh&&directAction('refresh')?'刷新合同状态':current?.presentation?.directActions?'刷新表单状态':'刷新状态';
  if(!current)return;
  if(current.queryOnly){
    for(const id of ['save','previewButton','submit','run','browser','recover','edit','discard'])$(id).hidden=true;
    $('fields').hidden=true;$('previewSection').hidden=true;document.querySelector('.steps').hidden=true;
    document.querySelector('.intro').textContent='查看已收到的请求及实际处理结果；本页不会发送消息或作出批准。';
    $('stageHint').textContent='';$('hint').hidden=true;$('refresh').hidden=false;$('refresh').textContent='刷新收件';
    $('state').textContent='只读查看';return;
  }
  $('hint').hidden=false;
  const direct=directAction(selectedAction());
  for(const id of ['save','previewButton','submit'])$(id).hidden=!!direct;
  $('run').hidden=!direct;
  $('refresh').hidden=!!direct&&selectedAction()==='refresh'&&!needsRefresh&&!revisionConflict;
  document.querySelector('.steps').hidden=!!direct||current.readOnly;
  document.querySelector('.intro').textContent=current.readOnly?'查看本次操作的实际状态；需要批准时，请核对独立确认卡片。':direct?'选择操作及交付期数后直接查看结果；验收通过与付款仍需您独立确认。':'补充所需信息，检查预览，再确认具体操作。';
  $('run').textContent=direct||'执行';
  $('run').disabled=busy||needsRefresh||!direct;
  $('recover').hidden=!current.canRecover;
  $('recover').textContent=current.result?.data?.confirmationRef?(current.values?.action==='pay'?'重新申请付款确认':'重新申请验收确认'):'沿用原需求重新报价';
  $('save').disabled=busy||needsRefresh||current.readOnly;
  $('previewButton').disabled=busy||needsRefresh||current.readOnly;
  $('submit').disabled=busy||needsRefresh||dirty||!current.preview||['completed','rejected','failed'].includes(current.phase);
  $('submit').textContent=current.readOnly?'按原操作继续':'提交并申请确认';
  $('browser').hidden=current.phase!=='waiting'||!current.result?.data?.confirmationRef;
  for(const node of $('fields').querySelectorAll('input,textarea,select,button'))node.disabled=busy||needsRefresh||current.readOnly;
  layout();
}
function preview(value){
  const target=$('preview');target.replaceChildren();
  const labels={...Object.fromEntries(Object.entries(current?.schema?.properties||{}).map(([key,item])=>[key,item.title||key])),title:'操作',account:'账号',assistant:'助手',description:'用途',allowedSenders:'谁可以发送',inputSchema:'消息格式',outputSchema:'回复格式',processing:'收到消息后怎么做',instructions:'处理规则',tools:'本地工具',enabled:'启用',previousRevision:'当前修订',previousDigest:'原内容摘要',newDigest:'新内容摘要'};
  // Fold only known explanatory/technical fields. Unknown business fields stay
  // visible, so new amounts, recipients or permissions cannot disappear silently.
  const detailed=new Set(['inputSchema','outputSchema','processing','instructions','previousRevision','previousDigest','newDigest','contentDigest']);
  const details=element('details');details.append(element('summary','查看详细信息'));
  let detailCount=0;
  for(const [key,item] of Object.entries(value?.preview||value||{})){
    const row=element('div',undefined,'preview-pair');row.append(element('strong',labels[key]||key));
    let displayed=item;
    if(key==='enabled')displayed=item?'是':'否';
    if(key==='allowedSenders'&&Array.isArray(item)&&!item.length)displayed='暂不允许任何人发送';
    if(key==='tools'&&Array.isArray(item)&&!item.length)displayed='不使用本地工具';
    if(detailed.has(key))row.append(typeof displayed==='object'?element('pre',JSON.stringify(displayed,null,2)):element('p',String(displayed)));
    else readableValue(row,displayed,current?.schema.properties?.[key]||{});
    if(key==='processing'||key==='instructions')evidencePair(target,'处理规则摘要',fieldSummary(key,item));
    if(key==='inputSchema'||key==='outputSchema')evidencePair(target,key==='inputSchema'?'需要提供的信息':'回复包含',schemaSummary(item));
    if(detailed.has(key)||technicalKeys.has(key)||(key==='name'&&current?.schema.properties?.inputSchema)){details.append(row);detailCount++;}
    else if(key==='description'&&typeof item==='string'&&item.length>160){
      evidencePair(target,labels[key],item.slice(0,160)+'…');details.append(row);detailCount++;
    }else target.append(row);
  }
  if(detailCount)target.append(details);
}

function render(form,error){
  if(error){problem(error);return;}
  if(!form)return;
  // Unsolicited host notifications must not overwrite edits or an in-flight submission.
  if(busy||dirty)return;
  if(current?.reference!==form.reference||current?.revision!==form.revision)editing=false;
  current=form;$('title').textContent=form.title;$('state').textContent=phases[form.phase]||form.phase;
  if(form.queryOnly){
    $('evidenceSection').hidden=false;$('resultSection').hidden=true;$('error').hidden=true;
    $('evidence').replaceChildren();renderInbox($('evidence'),form,values=>act('query',values));
    confirmationCard.show(form,null,()=>act('get'));buttons();return;
  }
  $('evidenceSection').hidden=!form.evidence||!Object.keys(form.evidence).length;
  $('evidence').replaceChildren();
  renderEvidence($('evidence'),form);
  $('fields').replaceChildren();editors={};
  const properties=form.schema.properties||{};
  const names=[...new Set([...(form.schema.required||[]),...Object.keys(properties)])];
  for(const name of names){
    const spec=properties[name];if(!spec)continue;
    const wrap=element('div',undefined,'field'),label=element('label',name==='tools'?'自动处理能力':spec.title||name,'label');
    if(name!=='tools'&&(form.schema.required||[]).includes(name))label.append(element('span','*','required'));
    let value=form.values[name];
    if(name==='profile'&&value===undefined&&spec.enum?.length===1)value=spec.enum[0];
    if(name==='name'&&value===undefined&&properties.inputSchema&&form.values.description)value='interface_'+form.reference.replace(/[^a-zA-Z0-9]/g,'').slice(0,16);
    const editor=fieldEditor(name,spec,value,form.readOnly);editor.node.id='field-'+name;label.htmlFor=editor.node.id;
    wrap.append(label,editor.node);
    if(spec.description){const help=element('details');help.append(element('summary','填写说明'),element('p',spec.description,'help'));wrap.append(help);}
    const detailed=['inputSchema','outputSchema','instructions','terms'].includes(name);
    const technical=name==='name'&&properties.inputSchema;
    if(name==='tools'){
      const group=element('details',undefined,'field-group');group.append(element('summary','高级设置：自动处理能力'),wrap);$('fields').append(group);
    }else if((detailed||technical)&&value!==undefined&&value!==''){
      const group=element('details',undefined,'field-group');
      group.append(element('summary','修改'+(spec.title||name)),wrap);
      const summary=element('div',undefined,'prepared-field');summary.append(element('strong',spec.title||name));
      if(name==='terms')readableValue(summary,value,spec);else summary.append(element('p',fieldSummary(name,value,spec),'field-summary'));
      $('fields').append(summary,group);
      if(name==='terms'&&(spec.required||[]).some(key=>value[key]===undefined))group.open=true;
    }else $('fields').append(wrap);
    editors[name]=editor;
  }
  $('previewSection').hidden=!form.preview;if(form.preview)preview(form.preview);
  $('resultSection').hidden=!form.result;
  const progress=domainProgress[form.kind.split('.')[0]]?.(form);
  $('resultTitle').textContent=progress?'本次操作':'处理状态';
  $('contractProgress').hidden=!progress;$('nextStep').hidden=!progress;
  $('contractProgress').textContent=progress?.contract||'';$('nextStep').textContent=progress?.next||'';
  if(form.result){$('result').textContent=JSON.stringify(form.result,null,2);$('resultText').textContent=form.phase==='waiting'?(form.result.data?.confirmationRef?'请打开受控确认页面，核对具体内容并作出决定。客户端会续接原操作。'+(transport.mode==='app'?'完成后返回这里点击“刷新状态”。':''):'正在等待原业务结果。'):(form.phase==='running'||form.phase==='accepted')?'请求已受理，正在等待业务处理结果；刷新只查询进度，不会重新提交。':form.phase==='unknown'?'原请求结果尚未核实，已停止自动推进。请让接入助手检查原任务，不要重新创建业务。':(phases[form.phase]||form.phase);}
  if(progress){
    $('resultSection').hidden=false;
    if(progress.state)$('state').textContent=progress.state;
    if(progress.operation)$('resultText').textContent=progress.operation;
  }
  if(!form.result)$('result').textContent='';
  if(form.result?.data?.reason==='expired'&&form.result.data.confirmationRef){
    $('state').textContent='确认已过期';
    $('resultText').textContent=form.values?.action==='pay'?'付款确认已过期，本期已经验收，无需重复验收。请点击“重新申请付款确认”，核对原合同与期次后重新提交。':'验收确认已过期，尚未因本次确认执行验收或付款。请点击“重新申请验收确认”，核对原合同与期次后重新提交。';
    problem(new Error($('resultText').textContent));
  }else if(form.result?.error){
    const error=form.result.error;
    problem(new Error(error.code==='expired'?'报价已过期，不能继续签约。'+(form.canRecover?'请点击“沿用原需求重新报价”，原需求与授权会保留。':'请核对原业务后恢复。'):(error.message||'操作未完成，请核对原结果。')));
  }else $('error').hidden=true;
  if(form.result?.data?.waitingFor==='original_quote_closure'){
    const data=form.result.data;
    $('resultText').textContent=data.message+' 旧报价最早安全结束时间：'+new Date(data.safeCloseAfter).toLocaleString();
  }
  const paymentResult=form.evidence?.['操作结果']||form.result?.data;
  if(!form.result?.data?.confirmationRef&&form.evidence?.['委托授权']?.authorized===false&&paymentResult?.payment==='unpaid'&&paymentResult.paymentError?.code==='expired'&&paymentResult.paymentError.message==='Explicit user renewal is required'){
    $('state').textContent='已验收，付款待续期';
    problem(new Error('本期已验收，付款因原委托授权过期被拒绝。请选择“续期原委托授权”；原操作等待中可让助手打开独立续期表单。续期生效后再恢复付款，无需重新验收。'));
  }
  const inlineConfirm=transport.mode==='app'&&supportsInlineConfirmation(form)&&form.phase==='waiting'&&form.result?.data?.confirmationRef;
  $('fields').hidden=!!inlineConfirm;
  if(inlineConfirm){$('previewSection').hidden=true;$('evidenceSection').hidden=true;}
  $('browser').textContent=inlineConfirm?'在浏览器确认':'打开确认页面';
  if(inlineConfirm)$('resultText').textContent='请在下方独立确认卡片核对并决定；允许运行工具不等于批准业务。决定后点击“刷新状态”查看实际处理结果。';
  confirmationCard.show(form,transport.confirmation,()=>act('get'));
  const confirmation=transport.confirmation;
  if(confirmation?.formReference===form.reference&&confirmation.revision===form.revision&&confirmation.reference===form.result?.data?.confirmationRef&&confirmation.status==='expired'){
    $('state').textContent='确认已过期';
    $('resultText').textContent='原业务确认已过期，刷新连接或更换浏览器不会延长期限。请按原业务流程处理。';
  }
  buttons();
}
$('edit').onclick=()=>{editing=true;buttons();};
$('fields').oninput=()=>{saveNotice('');dirty=true;editing=true;$('error').hidden=true;buttons();};
$('fields').onsubmit=event=>event.preventDefault();
function values(){const result={};for(const [key,editor]of Object.entries(editors)){try{const value=editor.read();if(value!==undefined)result[key]=value;}catch(error){error.field=key;throw error;}}return result;}
function checkInputs(input){
  for(const name of current.schema.required||[])if(input[name]===undefined||input[name]==='')throw Object.assign(new Error('请填写此项。'),{field:name});
}

function rememberDraft(){
  const copy=$('unsavedValues');copy.replaceChildren();
  for(const [name,value] of Object.entries(values())){
    const spec=current.schema.properties?.[name]||{};
    const index=spec.enum?.findIndex(item=>JSON.stringify(item)===JSON.stringify(value));
    const shown=index>=0?(spec.enumNames?.[index]??value):value;
    evidencePair(copy,spec.title||name,typeof shown==='object'?JSON.stringify(shown,null,2):shown);
  }
  $('unsavedCopy').hidden=false;
}
async function act(action,directValues){
  if(busy||!current||(needsRefresh&&action!=='get'))return;
  if(action==='update'&&!dirty){try{if(equalValues(values(),current.values)){saveNotice('没有新的修改需要保存。');$('error').hidden=true;return;}}catch(error){problem(error);return false;}}
  const direct=action==='direct'?directAction(directValues?.action):null;
  if(action==='direct'&&!direct)return false;
  const before=JSON.stringify(current);
  saveNotice(direct?'正在'+direct+'…':action==='get'?'正在刷新表单状态…':(action==='update'||action==='preview')&&dirty?'正在保存草稿…':'');
  busy=true;buttons();$('error').hidden=true;
  let form=current,saved=false,submitted=false;
  try{
    if(action==='query'){form=await transport.call('update',{reference:form.reference,revision:form.revision,values:directValues});}
    if(direct){
      // Use the same durable form path. A stale response must never submit a new stage.
      const reference=form.reference,revision=form.revision+1;
      const matches=value=>value?.reference===reference&&value.revision===revision&&
        JSON.stringify(value.values)===JSON.stringify(directValues)&&directAction(directValues.action,value);
      form=await transport.call('update',{reference,revision:form.revision,values:directValues});
      current=form;dirty=false;saved=true;
      $('unsavedCopy').hidden=true;$('unsavedValues').replaceChildren();
      if(!matches(form))throw Object.assign(new Error('Form revision changed'),{code:'id_conflict'});
      form=await transport.call('preview',{reference,revision});
      if(!matches(form))throw Object.assign(new Error('Form revision changed'),{code:'id_conflict'});
      submitted=true;
      form=await transport.call('submit',{reference,revision});
    }
    const input=(action==='update'||action==='preview')?values():null;
    if(action==='preview')checkInputs(input);
    if(input&&(dirty||!equalValues(input,form.values))){
      form=await transport.call('update',{reference:form.reference,revision:form.revision,values:input});
      current=form;dirty=false;saved=true;
      saveNotice('草稿已保存。');
      $('unsavedCopy').hidden=true;$('unsavedValues').replaceChildren();
    }
    if(!['update','query'].includes(action)&&!direct){submitted=action==='submit';form=await transport.call(action,{reference:form.reference,...(action==='get'?{}:{revision:form.revision})});}
    busy=false;dirty=false;needsRefresh=false;revisionConflict=false;if(action==='preview')editing=false;render(form);
    if(action==='query')saveNotice('查询结果已更新。');
    if(action==='get')saveNotice(before===JSON.stringify(form)?'已刷新，当前状态无变化。':'表单状态已更新。');
    if(action==='preview')saveNotice('检查通过，请核对预览；尚未提交。');
    if(direct){
      const done=['draft','completed'].includes(form.phase)&&!form.result?.error;
      saveNotice(done?(directValues.action==='refresh'?'合同状态已刷新。':direct+'已完成，请查看下方结果。'):
        ['accepted','running','waiting'].includes(form.phase)?direct+'已受理，等待原操作完成；请刷新表单状态查看进度。':direct+'未完成，请查看处理结果。');
      if(['unknown','failed','rejected'].includes(form.phase))needsRefresh=true;
      buttons();
    }
    return true;
  }catch(error){
    saveNotice(saved?'草稿已保存；后续操作未完成，请查看错误提示。':'');
    busy=false;
    if(error.code==='id_conflict'){
      revisionConflict=true;needsRefresh=true;
      if(dirty)rememberDraft();
    }else if(submitted)needsRefresh=true;
    // Keep any successful save even if the following preview failed.
    if(!dirty)render(form);
    problem(revisionConflict?new Error('本页版本已过期，刚才的保存或提交未生效。请点击“加载最新版本”；本页未保存内容已保留为核对副本，不会自动覆盖后台。'):error.code==='expired'?new Error('报价已过期，不能继续签约。请点击“刷新状态”，在原表单中选择“沿用原需求重新报价”。'):needsRefresh?new Error(error.message+' 请先刷新原表单状态，再决定是否继续。'):error);
    buttons();
    return false;
  }
}
$('recover').onclick=()=>act('recover');
$('save').onclick=()=>act('update');$('previewButton').onclick=()=>act('preview');$('submit').onclick=()=>act('submit');
$('run').onclick=()=>{try{act('direct',values());}catch(error){problem(error);}};
$('refresh').onclick=()=>{if(dirty&&!revisionConflict){problem(new Error('本页有未保存内容。请先执行当前选择、保存，或点击“放弃本页修改并刷新”。'));return;}if(!needsRefresh&&!revisionConflict&&directAction('refresh'))act('direct',{action:'refresh'});else act('get');};
$('discard').onclick=()=>{act('get');};
async function navigate(open){
  if(busy)return;
  busy=true;buttons();$('error').hidden=true;
  try{await open();}catch(error){problem(error);}
  finally{busy=false;buttons();}
}
$('browser').onclick=()=>navigate(()=>transport.openBrowser(current.reference));
$('setupButton').onclick=()=>navigate(()=>transport.openSetup());
buttons();transport.init(render).catch(problem);
setInterval(async()=>{
  if(transport.mode==='app'||!current||dirty||busy||polling||needsRefresh||!['accepted','running','waiting'].includes(current.phase))return;
  const original=current;polling=true;
  try{
    const form=await transport.call('get',{reference:original.reference});
    if(current===original&&!dirty&&!busy&&!needsRefresh&&JSON.stringify(form)!==JSON.stringify(current))render(form);
  }catch(error){if(current===original&&!dirty&&!busy)problem(error);}
  finally{polling=false;}
},2500);
