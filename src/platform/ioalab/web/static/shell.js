/* Shared navigation only; authentication and business operations retain their owners. */
'use strict';
(() => {
 const home=document.body.classList.contains('landing-home');
 const header=document.querySelector('.site-header,.topbar');if(!header)return;
 const logoURL=document.querySelector('link[rel="icon"]').getAttribute('href');
 function brandLink(className){
  const link=document.createElement('a');link.className=className+' ioa-brand';link.href='/';link.setAttribute('aria-label','九问IoA实验室首页');
  const mark=document.createElement('img');mark.src=logoURL;mark.alt='';mark.width=27;mark.height=27;mark.className='site-logo';
  const word=document.createElement('span');word.className='ioa-wordmark';word.append(document.createTextNode('ioa'));const dot=document.createElement('span');dot.className='ioa-dot';dot.textContent='.';word.append(dot);link.append(mark,word);return link;
 }
 const entries=[['智能体网络','/#network'],['如何接入','/connect'],['发现专业服务','/services'],['发现个人助手','/assistants'],['我的智能体','/workbench'],['管理者全局','/admin'],['在线试用','/trial']];
 if(!home){
  const side=document.createElement('aside');side.className='home-sidebar shell-sidebar';side.setAttribute('aria-label','页面导航');
  const logo=brandLink('side-monogram');side.append(logo);
  const label=document.createElement('p');label.className='side-caption';label.textContent='THE INTERNET OF AGENTS';side.append(label);
  const links=document.createElement('nav');for(const [text,path] of entries){const a=document.createElement('a');a.href=path;const word=document.createElement('span');word.textContent=text;a.append(word);if(location.pathname===path){a.classList.add('active');a.setAttribute('aria-current','page');}links.append(a);}side.append(links);
  const bottom=document.createElement('div');bottom.className='side-bottom';bottom.innerHTML='<span class="side-orbit" aria-hidden="true">✳</span><p>从独立的智能，<br>到连接的力量。</p><small>DISCOVER · CONNECT · CREATE</small>';side.append(bottom);document.body.prepend(side);
  header.classList.add('unified-header');
  const brand=brandLink('brand');const caption=document.createElement('span');caption.className='brand-caption';caption.textContent='九问IoA实验室';brand.append(caption);
  const old=header.querySelector('.brand');if(old)old.replaceWith(brand);else header.prepend(brand);
  header.querySelectorAll('.auth-back,.header-back,.header-back-placeholder').forEach(n=>n.remove());
  const nav=header.querySelector('nav');if(nav)nav.querySelectorAll('a').forEach(a=>{if(entries.some(([,path])=>path===a.getAttribute('href')))a.remove();});
 }
 function measure(){document.documentElement.style.setProperty('--shell-height',header.getBoundingClientRect().height+'px');}
 new ResizeObserver(measure).observe(header);measure();
})();
