const domainViews=Object.create(null);
function evidencePair(target,title,value){if(value===undefined||value===null)return;const row=element('div',undefined,'preview-pair');row.append(element('strong',title),element('p',String(value)));target.append(row);}
function evidenceDetail(target,title,value){const item=element('details');item.append(element('summary',title),element('pre',JSON.stringify(value,null,2)));target.append(item);}
function renderEvidence(target,form){const domain=form.kind.split('.')[0];if(domainViews[domain])domainViews[domain](target,form.evidence||{});else for(const [key,value]of Object.entries(form.evidence||{})){if(typeof value==='object')evidenceDetail(target,key,value);else evidencePair(target,key,value);}}
