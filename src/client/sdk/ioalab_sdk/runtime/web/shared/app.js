const transport = (()=>{
  let nextId=0,receive,origin=null,ready=false;
  const pending=new Map();
  const target=()=>origin&&origin!=='null'?origin:'*';
  function request(method,params) {
    return new Promise((resolve,reject)=>{
      const id=++nextId;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error('请求结果未知，请刷新原表单，不要重新创建业务。'));},35000);
      pending.set(id,{resolve,reject,timer});
      parent.postMessage({jsonrpc:'2.0',id,method,params},target());
    });
  }
  function unwrap(result) {
    if(result.isError)throw new Error(result.structuredContent?.error?.message||result.content?.[0]?.text||'操作未完成。');
    const value=result.structuredContent;
    if(value?.error)throw new Error(value.error.message);
    return value?.data;
  }
  window.addEventListener('message',event=>{
    if(event.source!==parent || (origin&&event.origin!==origin))return;
    const message=event.data;
    if(!message||message.jsonrpc!=='2.0')return;
    if(Object.hasOwn(message,'id')&&!message.method){
      const item=pending.get(message.id);if(!item)return;
      if(!origin)origin=event.origin;
      clearTimeout(item.timer);pending.delete(message.id);
      message.error?item.reject(new Error(message.error.message||'宿主请求失败。')):item.resolve(message.result);
    } else if(ready&&message.method==='ui/notifications/tool-result') {
      try {const form=unwrap(message.params);if(form?.reference&&form?.schema)receive(form);}catch(error){receive(null,error);}
    } else if(message.method==='ui/resource-teardown') {
      for(const item of pending.values()){clearTimeout(item.timer);item.reject(new Error('页面已关闭，请保留原表单引用。'));}pending.clear();
      parent.postMessage({jsonrpc:'2.0',id:message.id,result:{}},target());
    } else if(message.method==='ping') parent.postMessage({jsonrpc:'2.0',id:message.id,result:{}},target());
  });
  return {
    mode:'app',
    async init(callback) {
      receive=callback;
      if(parent===window)throw new Error('请通过支持 MCP Apps 的宿主打开，或使用客户端浏览器入口。');
      const result=await request('ui/initialize',{protocolVersion:'2026-01-26',appInfo:{name:'IoA Lab Forms',version:'1.0.0'},appCapabilities:{availableDisplayModes:['inline','fullscreen']}});
      if(result.protocolVersion!=='2026-01-26')throw new Error('宿主尚未支持此表单的 MCP Apps 版本。');
      ready=true;parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{}},target());
      new ResizeObserver(()=>parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/size-changed',params:{height:document.documentElement.scrollHeight}},target())).observe(document.body);
    },
    async call(action,args){return unwrap(await request('tools/call',{name:'ioa_'+({get:'get_form',update:'update_form',preview:'preview_form',submit:'submit_form'}[action]),arguments:args}));},
    async openBrowser(reference){unwrap(await request('tools/call',{name:'ioa_open_browser',arguments:{reference}}));},
    async openSetup(){unwrap(await request('tools/call',{name:'ioa_setup',arguments:{}}));},
  };
})();
