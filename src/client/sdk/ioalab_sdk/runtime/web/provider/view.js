domainViews.services=(target,evidence)=>{
  const facts=evidence['服务状态'];
  if(facts){evidencePair(target,'服务公示',facts.published?'已发布':'未发布');evidencePair(target,'搜索目录',facts.indexReady?'已就绪':'未就绪');evidencePair(target,'接单',facts.admissionPolicy?.accepting?'开放':'暂停');evidencePair(target,'在途委托',facts.admissionPolicy?.active);}
  for(const [key,value]of Object.entries(evidence)){
    if(key==='服务状态'){evidenceDetail(target,'服务完整记录',value);continue;}
    if(key==='可用分类'){evidenceDetail(target,'完整分类目录',value);continue;}
    if(typeof value==='object')readableEvidence(target,key,value);else evidencePair(target,key,value);
  }
};
