const transport = (()=>{
  let csrf=null;
  const reference=location.pathname.split('/').pop();
  async function request(path,body) {
    const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',
      headers:body?{'Content-Type':'application/json','X-Ioa-Csrf':csrf||''}:{},
      body:body?JSON.stringify(body):undefined});
    const value=await response.json();
    if(!response.ok)throw new Error(value.error?.message||'请求未完成，请检查原状态。');
    return value;
  }
  return {
    mode:'browser',
    async init(receive) {
      const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
      csrf=(token?await request('/user/session',{token}):await request('/user/api')).csrf;
      receive(await request('/user/forms/api',{action:'get',reference}));
    },
    call(action,args){return request('/user/forms/api',{action,...args});},
    openBrowser(){window.open('/user','_blank','noopener');return Promise.resolve();},
    openSetup(){return request('/user/setup/open',{});},
  };
})();
