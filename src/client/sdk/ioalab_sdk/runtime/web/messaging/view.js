domainViews.messaging=(target,evidence)=>{
  evidencePair(target,'处理状态',evidence.state);
  evidencePair(target,'交互状态',evidence.closed?'已结束':evidence.interactionRef?'交互已建立':undefined);
  if(evidence.result)readableEvidence(target,'对方的实际回复',evidence.result);
  for(const [key,value]of Object.entries(evidence)){
    if(['state','closed','interactionRef','result'].includes(key))continue;
    if(technicalKeys.has(key))evidenceDetail(target,'技术标识', {[key]:value});
    else if(key==='接口'&&Array.isArray(value)){
      for(const item of value)evidencePair(target,item.description||item.name,schemaSummary(item.inputSchema));
      evidenceDetail(target,'完整接口定义',value);
    }else if(key==='回复格式'){evidencePair(target,key,schemaSummary(value));evidenceDetail(target,'完整回复格式',value);}
    else if(typeof value==='object')readableEvidence(target,key,value);else evidencePair(target,key,value);
  }
};

function renderInbox(target,form,query){
  const data=form.evidence||{},states={received:'已收到',running:'正在处理',waiting:'等待处理',completed:'处理已完成',rejected:'已拒绝',unknown:'结果待核实',failed:'处理失败',pending:'等待处理'};
  function button(parent,label,filters){const node=element('button',label);node.type='button';node.onclick=()=>query(filters);parent.append(node);}
  function summary(parent,item){
    evidencePair(parent,'发送方（智能体标识）',item.senderAgentId);
    evidencePair(parent,'平台接收时间',item.acceptedAt?new Date(item.acceptedAt).toLocaleString():'未记录');
    evidencePair(parent,'处理状态',states[item.state]||item.state);
    evidencePair(parent,'回复投递',item.replyAccepted?'平台已受理回复':'尚未核实回复已投递');
  }
  if(data.detail){
    const item=data.detail;
    button(target,'返回收件列表',{});
    target.append(element('h3',item.description||item.interface));summary(target,item);
    readableEvidence(target,'请求内容',item.input,item.inputSchema);
    if(item.reply){
      readableEvidence(target,'实际回复',item.reply.output||item.reply.error,item.outputSchema);
      target.append(element('p','这里展示实际回复；回复中约定的后续确认或安排须按业务办理。','hint'));
    }
    evidenceDetail(target,'查看完整记录',item);return;
  }
  if(!data.items?.length)target.append(element('p','当前没有可查看的收到请求。'));
  for(const item of data.items||[]){
    const card=element('section');card.append(element('h3',item.description||item.interface));summary(card,item);
    for(const field of item.summary||[])evidencePair(card,field.label,field.value);
    button(card,'查看请求详情',{messageRef:item.messageRef});target.append(card);
  }
  if(form.values.before)button(target,'返回最新收件',{});
  if(data.nextBefore)button(target,'下一页',{before:data.nextBefore,limit:form.values.limit||20});
}
