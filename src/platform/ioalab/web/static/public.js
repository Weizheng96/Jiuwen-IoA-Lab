'use strict';
(()=>{
  const $=id=>document.getElementById(id),labels={online:'可接新委托',offline:'未连接',paused:'暂停接新委托',busy:'接单名额已满',listed:'待完成公示',unavailable:'模板不可用'};
  if(document.querySelector('.network-stats'))WebSession.api('/api/public/stats').then(data=>{
    for(const key of ['services','assistants','interactions']){const target=document.querySelector(`[data-public-stat="${key}"]`);if(target)target.textContent=Number.isSafeInteger(data[key])&&data[key]>=0?String(data[key]):'—';}
  }).catch(()=>{document.querySelector('.network-stats').setAttribute('title','统计暂不可用');});
  if(!$('public-catalog'))return;
  let services=[],names=new Map();
  function render(){
    const query=$('catalog-search').value.trim().toLowerCase(),category=$('catalog-category').value;
    const visible=services.filter(service=>(!category||service.category===category)&&(!query||(service.name+' '+service.description).toLowerCase().includes(query)));
    $('public-catalog').replaceChildren();
    $('catalog-status').textContent=visible.length?`${visible.length} 项公开服务`:'没有符合条件的公开服务。';
    for(const service of visible){const card=document.createElement('article');card.className='directory-card';
      const dot=document.createElement('i');dot.className='directory-dot red';
      const heading=document.createElement('h2');heading.textContent=service.name;
      const description=document.createElement('p');description.textContent=service.description;
      const status=document.createElement('small');status.textContent=(names.get(service.category)||'未分类')+' · '+(labels[service.status]||'状态未知');
      const states=document.createElement('p');states.className='catalog-states';states.textContent=`${service.connected?'已连接':'未连接'} · ${service.templateAvailable?'模板有效':'模板不可用'} · ${service.accepting?'允许接新委托':'暂停接新委托'}`;
      const guide=document.createElement('a');guide.href='/connect#interact';guide.textContent='如何委托 →';card.append(dot,heading,description,status,states,guide);$('public-catalog').append(card);
    }
  }
  $('catalog-search').addEventListener('input',render);$('catalog-category').addEventListener('change',render);
  Promise.all([WebSession.api('/api/public/catalog'),WebSession.api('/api/public/categories')]).then(([catalog,categories])=>{
    services=catalog;names=new Map(categories.map(item=>[item.id,item.name]));
    for(const category of categories){const option=document.createElement('option');option.value=category.id;option.textContent=category.name;$('catalog-category').append(option);}render();
  }).catch(()=>{$('catalog-status').textContent='公开服务暂时无法读取，请刷新后重试。';});
})();
