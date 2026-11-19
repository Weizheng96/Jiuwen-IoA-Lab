// Passwords go directly to the account-only local HTTP channel, never tools/call.
const accountApp = (() => {
  let entry=null, hostOrigin=null, nextId=0, receive, failure;
  const pending=new Map();
  const target=()=>hostOrigin&&hostOrigin!=='null'?hostOrigin:'*';
  function rpc(method, params) {
    return new Promise((resolve,reject)=>{
      const id=++nextId;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error('宿主连接超时，请重新打开账号接入界面。'));},35000);
      pending.set(id,{resolve,reject,timer});
      parent.postMessage({jsonrpc:'2.0',id,method,params},target());
    });
  }
  async function load(result) {
    const candidate=result?._meta?.['ioalab/account'];
    if(!candidate)return;
    const url=new URL(candidate.origin);
    if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.pathname!=='/'||url.search||url.hash||typeof candidate.token!=='string')throw new Error('账号页面入口无效，请重新打开。');
    entry=candidate;
    receive(await request('/user/api'));
  }
  addEventListener('message',async event=>{
    if(event.source!==parent||(hostOrigin&&event.origin!==hostOrigin))return;
    const m=event.data;if(!m||m.jsonrpc!=='2.0')return;
    if(Object.hasOwn(m,'id')&&!m.method){
      const item=pending.get(m.id);if(!item)return;
      if(!hostOrigin)hostOrigin=event.origin;
      clearTimeout(item.timer);pending.delete(m.id);
      m.error?item.reject(new Error('宿主未能打开账号界面。')):item.resolve(m.result);
    }else if(m.method==='ui/notifications/tool-result'){
      try{await load(m.params);}catch(e){e.accountConnection=true;failure(e);}
    }else if(m.method==='ui/resource-teardown'){
      entry=null;for(const input of document.querySelectorAll('input[type=password]'))input.value='';
      clearSecret();parent.postMessage({jsonrpc:'2.0',id:m.id,result:{}},target());
    }else if(m.method==='ping')parent.postMessage({jsonrpc:'2.0',id:m.id,result:{}},target());
  });
  async function request(path,body) {
    if(!entry)throw new Error('请等待账号页面连接，或重新打开原接入界面。');
    const allowed={'/user/api':'api','/user/setup':'setup','/user/credentials':'credentials'};
    if(!Object.hasOwn(allowed,path))throw new Error('此操作需在本机管理页面完成。');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const r=await fetch(entry.origin+'/user/app/'+allowed[path],{signal:controller.signal,method:body===undefined?'GET':'POST',credentials:'omit',referrerPolicy:'no-referrer',headers:{'Content-Type':'application/json','Authorization':'Bearer '+entry.token},...(body===undefined?{}:{body:JSON.stringify(body)})});
      const value=await r.json();
      if(!r.ok){const error=new Error(value.error?.message||'账号操作未完成，请刷新核实原请求。');error.accountConnection=r.status===403;throw error;}
      return value;
    }catch(error){if(error instanceof TypeError||error.name==='AbortError')error.accountConnection=true;throw error;}
    finally{clearTimeout(timer);}
  }
  return {request,async init(callback,onError){
    receive=callback;failure=onError;
    const fallback=document.createElement('button');fallback.textContent='在浏览器继续';fallback.type='button';
    fallback.onclick=async()=>{try{if(!entry?.navigationToken)throw new Error('请重新打开账号界面。');const result=await rpc('tools/call',{name:'ioa_setup_browser',arguments:{uiNavigationToken:entry.navigationToken}});if(result.isError||result.structuredContent?.error)throw new Error('浏览器入口尚未打开。');document.getElementById('message').textContent='已打开原账号页面，请在浏览器继续。';}catch(e){document.getElementById('message').textContent='本机连接不可用，请让接入助手打开原浏览器账号页。';}};
    document.getElementById('refresh').after(fallback);
    const result=await rpc('ui/initialize',{protocolVersion:'2026-01-26',appInfo:{name:'IoA Account',version:'1.0.0'},appCapabilities:{availableDisplayModes:['inline','fullscreen']}});
    if(result.protocolVersion!=='2026-01-26')throw new Error('宿主暂不支持账号内嵌页面，请使用浏览器接入。');
    parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{}},target());
    new ResizeObserver(()=>parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/size-changed',params:{height:document.documentElement.scrollHeight}},target())).observe(document.body);
  }};
})();
