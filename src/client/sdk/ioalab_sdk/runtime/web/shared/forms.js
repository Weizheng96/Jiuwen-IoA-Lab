let current=null, editors={},dirty=false,busy=false,needsRefresh=false;
const $=id=>document.getElementById(id);
const phases={draft:'草稿',previewed:'已预览',waiting:'等待确认',completed:'已完成',rejected:'已拒绝',failed:'失败',unknown:'待核实原结果',accepted:'已受理',running:'处理中'};
function problem(error){$('error').textContent=error.message||String(error);$('error').hidden=false;}
function buttons(){
  for(const id of ['save','previewButton','submit','refresh','browser','discard'])$(id).disabled=busy||!current;
  $('discard').hidden=!dirty;
  if(!current)return;
  $('save').disabled=busy||needsRefresh||current.readOnly;
  $('previewButton').disabled=busy||needsRefresh||current.readOnly;
  $('submit').disabled=busy||needsRefresh||dirty||!current.preview||['completed','rejected','failed'].includes(current.phase);
  $('submit').textContent=current.readOnly?'按原操作继续':'提交并申请确认';
  $('browser').hidden=current.phase!=='waiting'||!current.result?.data?.confirmationRef;
  for(const node of $('fields').querySelectorAll('input,textarea,select,button'))node.disabled=busy||needsRefresh||current.readOnly;
}
function preview(value){
  $('preview').replaceChildren();
  const labels={title:'操作',account:'账号',assistant:'助手',description:'用途',allowedSenders:'谁可以发送',inputSchema:'消息格式',outputSchema:'回复格式',processing:'收到消息后怎么做',tools:'本地工具',enabled:'启用',previousRevision:'当前修订',previousDigest:'原内容摘要',newDigest:'新内容摘要'};
  for(const [key,item] of Object.entries(value?.preview||value||{})){
    const row=element('div',undefined,'preview-pair');row.append(element('strong',labels[key]||key));
    row.append(typeof item==='object'?element('pre',JSON.stringify(item,null,2)):element('p',String(item)));$('preview').append(row);
  }
}
function render(form,error){
  if(error){problem(error);return;}
  if(!form)return;
  // Unsolicited host notifications must not overwrite edits or an in-flight submission.
  if(busy||dirty)return;
  current=form;$('title').textContent=form.title;$('state').textContent=phases[form.phase]||form.phase;
  $('evidenceSection').hidden=!form.evidence||!Object.keys(form.evidence).length;
  $('evidence').replaceChildren();
  renderEvidence($('evidence'),form);
  $('fields').replaceChildren();editors={};
  const properties=form.schema.properties||{};
  const names=[...new Set([...(form.schema.required||[]),...Object.keys(properties)])];
  for(const name of names){
    const spec=properties[name];if(!spec)continue;
    const wrap=element('div',undefined,'field'),label=element('label',spec.title||name,'label');
    if((form.schema.required||[]).includes(name))label.append(element('span','*','required'));
    const editor=fieldEditor(name,spec,form.values[name],form.readOnly);editor.node.id='field-'+name;label.htmlFor=editor.node.id;
    wrap.append(label,editor.node);if(spec.description)wrap.append(element('p',spec.description,'help'));
    $('fields').append(wrap);editors[name]=editor;
  }
  $('previewSection').hidden=!form.preview;if(form.preview)preview(form.preview);
  $('resultSection').hidden=!form.result;
  if(form.result){$('result').textContent=JSON.stringify(form.result,null,2);$('resultText').textContent=form.phase==='waiting'?(form.result.data?.confirmationRef?'请打开受控确认页面，核对具体内容并作出决定。客户端会续接原操作。':'正在等待原业务结果。'):(phases[form.phase]||form.phase);}
  buttons();
}
$('fields').oninput=()=>{dirty=true;$('error').hidden=true;buttons();};
$('fields').onsubmit=event=>event.preventDefault();
function values(){const result={};for(const [key,editor]of Object.entries(editors)){const value=editor.read();if(value!==undefined)result[key]=value;}return result;}
async function act(action){
  if(busy||!current||(needsRefresh&&action!=='get'))return;
  busy=true;buttons();$('error').hidden=true;
  let form=current;
  try{
    if((action==='update'||action==='preview')&&dirty){
      form=await transport.call('update',{reference:form.reference,revision:form.revision,values:values()});
      current=form;dirty=false;
    }
    if(action!=='update')form=await transport.call(action,{reference:form.reference,...(action==='get'?{}:{revision:form.revision})});
    busy=false;dirty=false;needsRefresh=false;render(form);
  }catch(error){
    busy=false;
    if(action==='submit')needsRefresh=true;
    // Keep any successful save even if the following preview failed.
    if(!dirty)render(form);
    problem(needsRefresh?new Error(error.message+' 请先刷新原表单状态，再决定是否继续。'):error);
    buttons();
  }
}
$('save').onclick=()=>act('update');$('previewButton').onclick=()=>act('preview');$('submit').onclick=()=>act('submit');
$('refresh').onclick=()=>{if(dirty){problem(new Error('先保存当前草稿，再刷新状态。'));return;}act('get');};
$('discard').onclick=()=>act('get');
$('browser').onclick=async()=>{try{await transport.openBrowser(current.reference);}catch(error){problem(error);}};
$('setupButton').onclick=async()=>{try{await transport.openSetup();}catch(error){problem(error);}};
buttons();transport.init(render).catch(problem);
setInterval(()=>{if(current&&!dirty&&!busy&&!needsRefresh&&['accepted','running','waiting'].includes(current.phase))act('get');},2500);
