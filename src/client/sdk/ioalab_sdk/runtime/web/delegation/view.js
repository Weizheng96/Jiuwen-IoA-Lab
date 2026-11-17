domainViews.delegation=(target,evidence,presentation)=>{const facts=evidence['合同状态'];if(facts){evidencePair(target,'合同',facts.formed?'已成约':'待成约');evidencePair(target,'开工',facts.started?'已开工':'待开工');paymentView(target,facts);if(facts.stepRequest)evidenceDetail(target,'需要您决定的合同环节',facts.stepRequest);}if(evidence['报价'])for(const quote of evidence['报价']){evidencePair(target,'候选报价',presentation?.choices?.quoteRef?.[quote.quoteRef]||'报价信息待刷新');evidenceDetail(target,'真实报价与条款',quote.card);}for(const [key,value]of Object.entries(evidence)){if(key==='报价')continue;if(typeof value==='object'){evidenceDetail(target,key+'：完整依据',value);if(key==='操作结果')target.lastElementChild.open=true;}else evidencePair(target,key,value);}};
domainProgress.delegation=form=>{
  const facts=form.evidence?.['合同状态'];
  if(!facts)return null;
  const accepted=facts.acceptedPeriods;
  const acceptance=accepted===0?'尚未确认接受交付':Number.isInteger(accepted)&&accepted>0?'已确认接受'+accepted+'期交付':'交付接受状态待核对';
  const payment=({paid:'已付清',partial:'部分付款',unpaid:'尚未付款'})[facts.payment]||'付款状态待核对';
  const progress={contract:'合同进度：'+acceptance+'；'+payment+'。',
    next:'下一步：核对交付及检查结果，再选择操作；确认接受交付和付款仍需独立确认。'};
  if(facts.closed)progress.next='下一步：合同已结束，可查看原交付、验收及付款记录。';
  else if(['accepted','running','waiting'].includes(form.phase))progress.next='下一步：等待当前操作完成；如有独立确认，请核对并作出决定，再刷新表单状态。';
  else if(['unknown','failed','rejected'].includes(form.phase))progress.next='下一步：先核对本次操作的实际结果及原因，不要重复提交。';
  if(form.phase==='draft'){
    progress.state=facts.closed?'合同已结束':'等待选择下一步';
    if(form.result?.status==='completed'){
      const result=form.evidence?.['操作结果'];
      const report=result?.report;
      const period=Number.isInteger(result?.period)&&result.period>0?'第'+result.period+'期':'';
      progress.operation=report?(period+'验收检查已完成，'+(report.status==='pass'?'全部通过。':report.status==='fail'?'检查未通过，请查看报告。':'结果待核对，请查看报告。')):
        result?'本次操作已完成，请查看操作结果。':'合同状态已更新。';
    }else progress.operation='当前选择尚未提交，请准备下一步操作。';
  }
  return progress;
};
