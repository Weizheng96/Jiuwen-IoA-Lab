const domainViews=Object.create(null);
const domainProgress=Object.create(null);
function evidencePair(target,title,value){if(value===undefined||value===null)return;const row=element('div',undefined,'preview-pair');row.append(element('strong',title),element('p',String(value)));target.append(row);}
function evidenceDetail(target,title,value){const item=element('details');item.append(element('summary',title),element('pre',JSON.stringify(value,null,2)));target.append(item);}
function renderEvidence(target,form){const domain=form.kind.split('.')[0];if(domainViews[domain])domainViews[domain](target,form.evidence||{},form.presentation||{});else for(const [key,value]of Object.entries(form.evidence||{})){if(typeof value==='object')evidenceDetail(target,key,value);else evidencePair(target,key,value);}}

// Deterministic presentation of the original values; no model-generated facts.
const fieldNames={name:'名称',description:'说明',input:'请求内容',output:'回复内容',result:'结果',message:'说明',status:'状态',topic:'主题',startTime:'开始时间',endTime:'结束时间',timezone:'时区',account:'账号',profile:'身份',terms:'交易条款',formation:'交易方式',deliverables:'交付内容',inputs:'所需材料',pricing:'计价条款',currency:'币种',proposalFee:'拟卡费',depositFormula:'订金公式',paymentWeights:'付款权重',priceFormula:'价格公式',priceFactors:'价格参数',deadlines:'期限',respondWithin:'报价响应时限',startWithin:'开工时限',deliverWithin:'交付时限',acceptWithin:'验收时限',acceptance:'验收要求',authorizableSteps:'需另行授权的环节',amount:'金额',total:'总额',payment:'付款状态',period:'交付期次',validDays:'有效天数',tags:'关键词',categoryId:'服务分类',tools:'本地工具',allowedSenders:'谁可以发送',enabled:'发布后启用'};
const technicalKeys=new Set(['reference','revision','interfaceRef','quoteRef','taskRef','contractRef','messageRef','confirmationRef','contentDigest','previousDigest','newDigest','previousRevision','bindingDigest','operationId','signature','signatures','proof','jwks','jwk']);
function displayValue(value,spec={}){
  const index=spec.enum?.findIndex(item=>JSON.stringify(item)===JSON.stringify(value));
  if(index>=0&&spec.enumNames?.[index])return spec.enumNames[index];
  return value===null?'未指定':typeof value==='boolean'?(value?'是':'否'):String(value);
}
function readableValue(target,value,spec={}){
  if(value&&typeof value==='object'){
    if(!Object.keys(value).length){target.append(element('p',Array.isArray(value)?'无':'未填写'));return;}
    const advanced={};
    for(const [key,child]of Object.entries(value)){
      if(technicalKeys.has(key)){advanced[key]=child;continue;}
      const childSpec=Array.isArray(value)?spec.items||{}:spec.properties?.[key]||{};
      const row=element('div',undefined,'value-item');
      row.append(element('strong',Array.isArray(value)?'第'+(Number(key)+1)+'项':childSpec.title||fieldNames[key]||key));
      if(['inputSchema','outputSchema','specSchema'].includes(key))row.append(element('p',schemaSummary(child)));
      else readableValue(row,child,childSpec);
      target.append(row);
    }
    if(Object.keys(advanced).length)evidenceDetail(target,'技术标识',advanced);
  }else target.append(element('p',displayValue(value,spec)));
}
function schemaSummary(value){
  const properties=value?.properties||value||{};
  const names=Object.entries(properties).filter(([,v])=>v&&typeof v==='object').map(([key,v])=>(v.title||key)+(value?.required?.includes(key)?'（必填）':''));
  return names.length?'包含：'+names.join('、'):'尚未定义字段';
}
function fieldSummary(name,value,spec={}){
  if(['inputSchema','outputSchema','specSchema'].includes(name))return schemaSummary(value);
  if(typeof value==='string')return value.length>100?value.slice(0,100)+'…':value;
  if(Array.isArray(value))return value.length?'已填写 '+value.length+' 项':'无';
  if(value&&typeof value==='object')return '已填写 '+Object.keys(value).length+' 个字段';
  return displayValue(value,spec);
}
function readableEvidence(target,title,value,spec={}){
  const section=element('div',undefined,'business-summary');section.append(element('h3',title));
  readableValue(section,value,spec);evidenceDetail(section,'查看原始数据',value);target.append(section);
}
