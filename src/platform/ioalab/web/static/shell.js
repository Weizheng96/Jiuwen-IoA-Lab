/* Common browser header; existing workbench control IDs stay intact. */
'use strict';
(() => {
  const header=document.querySelector('.site-header,.topbar');
  if(!header)return;
  header.classList.add('unified-header');
  const brand=document.createElement('a');brand.className='brand';brand.href='/';brand.setAttribute('aria-label','九问IoA实验室首页');
  const mark=document.createElement('img');mark.src='/static/logo.svg';mark.alt='';mark.width=36;mark.height=40;mark.className='site-logo';
  const words=document.createElement('span');words.className='brand-words';words.textContent='九问IoA实验室';
  const english=document.createElement('small');english.textContent='JIUWEN IoA LAB';words.append(english);brand.append(mark,words);
  const oldBrand=header.querySelector('.brand');if(oldBrand)oldBrand.replaceWith(brand);else header.prepend(brand);
  header.querySelectorAll('.auth-back').forEach(link=>link.remove());
  document.querySelectorAll('main .back-link').forEach(link=>{if(new URL(link.href,location.href).pathname==='/')link.remove();});
  const home=location.pathname==='/';
  const back=document.createElement(home?'span':'a');back.className=home?'header-back-placeholder':'header-back';
  if(home)back.setAttribute('aria-hidden','true');
  else{back.href='/';back.setAttribute('aria-label','返回首页');back.innerHTML='<span aria-hidden="true">←</span><span>返回</span>';}
  header.prepend(back);
  const nav=header.querySelector('nav');
  if(nav&&!nav.querySelector('a[href="/connect"]')){
    const guide=document.createElement('a');guide.href='/connect';guide.textContent='如何接入';
    nav.insertBefore(guide,nav.querySelector('.nav-owned,.auth-actions'));
  }
  if(!nav){const guide=document.createElement('a');guide.href='/connect';guide.className='header-help';guide.textContent='如何接入';header.append(guide);}
  function measureHeader(){
    document.documentElement.style.setProperty('--shell-height',header.getBoundingClientRect().height+'px');
  }
  const observer=new ResizeObserver(measureHeader);observer.observe(header);measureHeader();
})();
