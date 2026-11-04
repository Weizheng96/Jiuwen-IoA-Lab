/* Browser identity is issued by the server's HttpOnly session cookie. */
'use strict';
window.WebSession=(()=>{
  const pages=new Set(['/','/services','/assistants','/connect','/workbench','/admin','/trial']);
  let current=null,sessionError=null;
  async function api(path,body){
    const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{Accept:'application/json'}:{Accept:'application/json','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok){const error=Error(data.error?.message||'请求失败，请重试。');error.status=response.status;throw error;}
    return data;
  }
  function safeNext(value){
    try{const url=new URL(value||'/workbench',location.origin);if(url.origin!==location.origin||url.username||url.password||!pages.has(url.pathname))return '/workbench';
      const target=new URL(url.pathname,location.origin);
      if(url.pathname==='/workbench'&&['assistant','provider'].includes(url.searchParams.get('view')))target.searchParams.set('view',url.searchParams.get('view'));
      if(url.pathname==='/admin'&&url.searchParams.get('view')==='panorama')target.searchParams.set('view','panorama');
      return target.pathname+target.search+url.hash;
    }catch{return '/workbench';}
  }
  function loginUrl(next){return '/login?'+new URLSearchParams({next:safeNext(next)});}
  function render(){
    const nav=document.querySelector('.site-header nav');if(!nav)return;
    let box=nav.querySelector('.auth-actions');if(!box){box=document.createElement('div');box.className='auth-actions';nav.append(box);}box.replaceChildren();
    if(sessionError){const retry=document.createElement('button');retry.className='auth-exit';retry.textContent='会话状态未知 · 重试';retry.onclick=()=>refresh().catch(()=>{});box.append(retry);return;}
    if(current){const name=document.createElement('span');name.className='auth-username';name.textContent=current.username;const exit=document.createElement('button');exit.className='auth-exit';exit.textContent='退出';exit.onclick=()=>logout().catch(error=>{exit.textContent=error.message;});box.append(name,exit);}
    else {for(const [text,path] of [['登录','/login'],['注册','/register']]){const link=document.createElement('a');link.href=path;link.textContent=text;link.className=text==='登录'?'nav-login':'nav-register';box.append(link);}}
  }
  async function refresh(){
    try {const value=await api('/api/session');
      if(!value.role)current=null;
      else {const profile=value.id?await api('/api/me'):{username:'管理员'};current={...value,username:profile.username};}
      sessionError=null;render();return current;
    }catch(error){current=null;sessionError=error;render();throw error;}
  }
  async function logout(){await api('/api/auth/logout',{});current=null;location.assign('/');}
  const ready=refresh().catch(()=>null);
  document.addEventListener('DOMContentLoaded',render);
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||link.target==='_blank')return;
    const url=new URL(link.href,location.href);if(url.origin!==location.origin||!['/workbench','/admin'].includes(url.pathname))return;
    event.preventDefault();ready.then(()=>location.assign(current?url.pathname+url.search+url.hash:loginUrl(url.href)));
  });
  addEventListener('pageshow',event=>{if(event.persisted)refresh().then(value=>{if(!value&&['/workbench','/admin'].includes(location.pathname))location.replace(loginUrl(location.href));}).catch(()=>{});});
  return {api,ready,refresh,logout,safeNext,loginUrl};
})();
