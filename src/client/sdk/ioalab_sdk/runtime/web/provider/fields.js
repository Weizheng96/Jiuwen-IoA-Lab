// A2C value domains are a field map, not a JSON Schema root object.
function specEditor(value, disabled) {
  const original=value||{},node=element('div',undefined,'schema-box'),rows=element('div'),items=[];
  let edited=false,rawEdited=false;
  function add(name='',domain={type:'string',maxLength:1000}) {
    const row=element('div',undefined,'schema-row'),key=element('input'),raw=element('textarea');
    key.value=name;key.placeholder='字段名，如 topic';key.setAttribute('aria-label','规格字段名');
    raw.value=JSON.stringify(domain);raw.setAttribute('aria-label','字段约束');
    key.disabled=raw.disabled=disabled;
    const remove=element('button','删除规格字段');remove.type='button';remove.disabled=disabled;
    const item={row,key,raw};items.push(item);
    remove.onclick=()=>{edited=true;items.splice(items.indexOf(item),1);row.remove();node.dispatchEvent(new Event('input',{bubbles:true}));};
    row.oninput=()=>{edited=true;};row.append(key,raw,remove);rows.append(row);
  }
  for(const [key,domain] of Object.entries(original))add(key,domain);
  const addButton=element('button','＋ 添加规格字段');addButton.type='button';addButton.disabled=disabled;
  addButton.onclick=()=>{add();edited=true;node.dispatchEvent(new Event('input',{bubbles:true}));};
  const advanced=element('details'),raw=element('textarea');raw.value=JSON.stringify(original,null,2);
  raw.disabled=disabled;raw.setAttribute('aria-label','A2C 规格字段表');raw.oninput=()=>{rawEdited=true;};
  advanced.append(element('summary','高级编辑：规格字段表'),raw);
  function readRows(){
    if(!edited)return original;
    const result=Object.create(null);
    for(const item of items){const name=item.key.value.trim();if(!name||Object.hasOwn(result,name))throw new Error('规格字段名不能为空或重复。');result[name]=JSON.parse(item.raw.value);}
    return result;
  }
  advanced.ontoggle=()=>{if(advanced.open&&!rawEdited)raw.value=JSON.stringify(readRows(),null,2);};
  node.append(rows,addButton,element('p','每项填写字段名和约束。文本需 maxLength 或 date 格式；可用 enum 限定选项、const 声明固定值。固定值由模板提供，其余字段均由委托方填写。','help'),advanced);
  return {node,read:()=>rawEdited?JSON.parse(raw.value):readRows()};
}
