function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function schemaEditor(value, disabled) {
  const original = value || {type:'object', properties:{}, required:[], additionalProperties:false};
  const box = element('div', undefined, 'schema-box');
  const rows = element('div');
  const controls = [];
  let edited = false;
  function add(name='', schema={type:'string'}, required=false) {
    const row = element('div', undefined, 'schema-row');
    const key = element('input'); key.value=name; key.placeholder='字段名，如 time'; key.disabled=disabled;
    key.setAttribute('aria-label','消息字段名');
    const type = element('select'); type.disabled=disabled; type.setAttribute('aria-label','字段类型');
    for (const [kind,label] of [['string','文本'],['number','数字'],['integer','整数'],['boolean','是/否'],['object','对象'],['array','列表']]) {
      const option=element('option',label); option.value=kind; type.append(option);
    }
    type.value = typeof schema.type === 'string' ? schema.type : 'string';
    const label=element('label',undefined,'schema-required'); const needed=element('input'); needed.type='checkbox'; needed.checked=required; needed.disabled=disabled;
    label.append(needed,document.createTextNode(' 必填'));
    const remove=element('button','删除'); remove.type='button'; remove.disabled=disabled;
    const item={row,key,type,needed,schema}; controls.push(item);
    remove.onclick=()=>{edited=true;controls.splice(controls.indexOf(item),1);row.remove();box.dispatchEvent(new Event('input',{bubbles:true}));};
    row.oninput=()=>{edited=true;}; row.append(key,type,label,remove);rows.append(row);
  }
  for (const [name,schema] of Object.entries(original.properties || {})) add(name,schema,(original.required||[]).includes(name));
  const button=element('button','＋ 添加字段');button.type='button';button.disabled=disabled;button.onclick=()=>{add();edited=true;box.dispatchEvent(new Event('input',{bubbles:true}));};
  box.append(rows,button,element('p','可在高级编辑中设置嵌套字段、长度、格式及其他约束。','help'));
  const advanced=element('details');advanced.append(element('summary','高级编辑：JSON Schema'));
  const raw=element('textarea');raw.className='json';raw.value=JSON.stringify(original,null,2);raw.disabled=disabled;raw.setAttribute('aria-label','JSON Schema');
  let rawEdited=false;raw.oninput=()=>{rawEdited=true;};advanced.append(raw);box.append(advanced);
  function readRows() {
    if (!edited) return original;
    const properties=Object.create(null),required=[];
    for (const item of controls) {
      const name=item.key.value.trim();
      if (!name || Object.hasOwn(properties,name)) throw new Error('消息字段名不能为空或重复。');
      properties[name]=item.type.value===item.schema.type ? {...item.schema} : {type:item.type.value};
      if(item.needed.checked)required.push(name);
    }
    return {...original,properties,required};
  }
  advanced.ontoggle=()=>{if(advanced.open&&!rawEdited)raw.value=JSON.stringify(readRows(),null,2);};
  return {node:box,read:()=>rawEdited?JSON.parse(raw.value):readRows()};
}

let fieldCounter=0;
function fieldEditor(name,spec,value,disabled) {
  if (spec.enum) {
    const node=element('select');node.disabled=disabled;
    const empty=element('option','请选择');empty.value='';node.append(empty);
    spec.enum.forEach((item,index)=>{const option=element('option',spec.enumNames?.[index]||String(item));option.value=String(index);node.append(option);});
    node.value=value===undefined?'':String(spec.enum.findIndex(item=>item===value));
    return {node,read:()=>node.value===''?undefined:spec.enum[Number(node.value)]};
  }
  if (name==='specSchema') return specEditor(value,disabled);
  if (name==='inputSchema'||name==='outputSchema') return schemaEditor(value,disabled);
  if (name==='allowedSenders') {
    const node=element('div'),choice=element('select'),accounts=element('textarea');
    choice.setAttribute('aria-label','谁可以发送');accounts.setAttribute('aria-label','允许发送的账号');
    for(const [key,label] of [['selected','指定账号'],['nobody','暂不允许任何人'],['everyone','所有已登录账号']]){
      const option=element('option',label);option.value=key;choice.append(option);
    }
    choice.value=value===null?'everyone':value?.length?'selected':'nobody';
    accounts.value=Array.isArray(value)?value.join('\n'):'';accounts.placeholder='每行一个准确账号';
    choice.disabled=accounts.disabled=disabled;
    const toggle=()=>{accounts.hidden=choice.value!=='selected';};choice.onchange=toggle;toggle();
    node.append(choice,accounts);
    return {node,read:()=>{
      if(choice.value==='everyone')return null;
      if(choice.value==='nobody')return [];
      const names=accounts.value.split('\n').map(item=>item.trim()).filter(Boolean);
      if(!names.length)throw new Error('请填写允许发送的准确账号，或选择暂不允许任何人。');
      return [...new Set(names)];
    }};
  }
  if (name==='tools') {
    const node=element('div'), status=element('p',undefined,'help'), choices=[];
    const available=current?.presentation?.tools||[];
    const selected=Array.isArray(value)?value:[];
    node.append(status);
    const update=()=>{status.textContent=choices.some(item=>item.control.checked)?'仅允许使用已勾选的能力；发布仍需确认。':'仅接收并回复，不调用本地工具。';};
    function add(name,label,description,checked) {
      const row=element('label'),control=element('input');control.type='checkbox';control.checked=checked;control.disabled=disabled;
      row.append(control,document.createTextNode(' '+label));node.append(row);
      if(description)node.append(element('p',description,'help'));
      choices.push({name,control});control.onchange=update;
    }
    available.forEach((item,index)=>add(item.name,item.description||('已配置能力 '+(index+1)),null,selected.includes(item.name)));
    selected.filter(name=>!available.some(item=>item.name===name)).forEach((name,index)=>
      add(name,'原有能力 '+(index+1)+'（当前不可用）','保留原设置；如不再需要，可取消勾选。',true));
    if(!available.length)node.append(element('p','暂未配置日历、邮件等自动处理能力，无需填写。','help'));
    update();return {node,read:()=>choices.filter(item=>item.control.checked).map(item=>item.name)};
  }
  const kind=spec.type;
  if (kind==='array'&&spec.items?.type==='object'&&spec.items.properties) {
    const node=element('div'),rows=element('div'),items=[];node.append(rows);
    function add(value){const editor=fieldEditor(name+'Item',spec.items,value,disabled),row=element('div',undefined,'schema-box');const remove=element('button','删除此项');remove.type='button';remove.disabled=disabled;const item={editor,row};items.push(item);remove.onclick=()=>{items.splice(items.indexOf(item),1);row.remove();node.dispatchEvent(new Event('input',{bubbles:true}));};row.append(editor.node,remove);rows.append(row);}
    for(const item of value||[])add(item);
    const button=element('button','＋ 添加一项');button.type='button';button.disabled=disabled;button.onclick=()=>{add({});node.dispatchEvent(new Event('input',{bubbles:true}));};node.append(button);
    return {node,read:()=>items.map(item=>item.editor.read())};
  }
  if (kind==='array'&&spec.items?.type==='string') {
    const node=element('textarea');node.disabled=disabled;node.value=(value||[]).join('\n');node.placeholder='每行一项';return {node,read:()=>node.value.split('\n').map(v=>v.trim()).filter(Boolean)};
  }
  if (kind==='object'&&spec.properties) {
    const node=element('fieldset'),children={};
    node.append(element('legend',spec.title||name));
    for(const [key,child] of Object.entries(spec.properties)){
      const wrap=element('div',undefined,'field'),label=element('label',child.title||key);
      const editor=fieldEditor(key,child,value?.[key],disabled);children[key]=editor;
      editor.node.id='ioa-nested-'+(++fieldCounter);label.htmlFor=editor.node.id;
      wrap.append(label,editor.node);node.append(wrap);
    }
    return {node,read:()=>{const result=Object.create(null);for(const [key,editor]of Object.entries(children)){const item=editor.read();if(item!==undefined)result[key]=item;}return value===undefined&&Object.keys(children).length&&!Object.keys(result).length?undefined:result;}};
  }
  if (kind==='boolean') {const node=element('input');node.type='checkbox';node.checked=Boolean(value);node.disabled=disabled;return {node,read:()=>node.checked};}
  const complex=!kind||Array.isArray(kind)||kind==='array'||kind==='object';
  const node=element(complex||name==='instructions'||name==='description'?'textarea':'input');
  node.disabled=disabled;
  if(complex){node.setAttribute('aria-label',spec.title||name);node.className='json';node.value=value===undefined?'':JSON.stringify(value,null,2);}
  else node.value=value===undefined?'':String(value);
  if(kind==='number'||kind==='integer'){node.type='number';node.step=kind==='integer'?'1':'any';if(spec.minimum!==undefined)node.min=spec.minimum;if(spec.maximum!==undefined)node.max=spec.maximum;}
  if(kind==='string'&&spec.format==='email')node.type='email';
  if(kind==='string'&&spec.format==='date-time')node.placeholder='例如 2026-11-18T14:00:00+08:00（含时区）';
  let container=node;
  if(complex){container=element('details',undefined,'json-editor');container.open=value===undefined;
    container.append(element('summary',value===undefined?'补充'+(spec.title||name):'编辑'+(spec.title||name)+'（高级）'),node);}
  return {node:container,read:()=>{
    if(!node.value.trim())return undefined;
    if(complex)return JSON.parse(node.value);
    if(kind==='number'||kind==='integer')return Number(node.value);
    return node.value;
  }};
}
