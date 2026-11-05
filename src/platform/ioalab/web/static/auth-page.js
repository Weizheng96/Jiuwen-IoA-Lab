'use strict';
(()=>{
  const $=id=>document.getElementById(id),params=new URLSearchParams(location.search),next=WebSession.safeNext(params.get('next'));
  let mode=location.pathname==='/register'?'register':'login',pending=null,busy=false,config=null;
  function updateMode(value){
    if(busy)return;
    mode=value==='register'?'register':'login';const registering=mode==='register';
    $('tab-login').setAttribute('aria-pressed',String(!registering));$('tab-register').setAttribute('aria-pressed',String(registering));
    $('auth-title').textContent=registering?'加入九问IoA实验室':'欢迎回来';$('auth-subtitle').textContent=registering?'创建账号，开启你的智能体网络。':'登录后，查看和管理你的智能体。';
    $('confirm-field').hidden=!registering;$('confirm-password').required=registering;$('account-password').autocomplete=registering?'new-password':'current-password';
    $('account-submit').textContent=registering?'注册并进入':'登录';$('auth-error').hidden=true;
    $('invite-field').hidden=!(registering&&config?.registration==='invite');
    $('registration-note').textContent=registering&&config?.registration==='closed'?'当前未开放注册，请联系实验室管理员。':'';
    $('account-submit').disabled=registering&&config?.registration==='closed';
    document.title=(registering?'注册':'登录')+' · 九问IoA实验室';history.replaceState(null,'',(registering?'/register':'/login')+'?'+new URLSearchParams({next}));
  }
  $('tab-login').onclick=()=>updateMode('login');$('tab-register').onclick=()=>updateMode('register');
  $('toggle-password').onclick=()=>{const visible=$('account-password').type==='password';$('account-password').type=visible?'text':'password';$('toggle-password').textContent=visible?'隐藏':'显示';$('toggle-password').setAttribute('aria-label',visible?'隐藏密码':'显示密码');$('toggle-password').setAttribute('aria-pressed',String(visible));};
  function error(message){$('auth-error').textContent=message;$('auth-error').hidden=false;}
  $('account-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    const username=$('account-name').value.trim(),password=$('account-password').value,invite=$('account-invite').value.trim();
    if(!/^[\w.@+-]{3,128}$/.test(username)){error('账号须为 3–128 位字母、数字或 _ . @ + -。');return;}
    if(!password){error('请输入密码。');return;}
    if(mode==='register'&&password!==$('confirm-password').value){error('两次输入的密码不一致。');return;}
    // A stable ID survives uncertain replies and reloads, without persisting credentials.
    const fingerprint=JSON.stringify([mode,username,password,invite]);
    if(pending&&pending.fingerprint!==fingerprint){error('上次操作结果尚待核对，请恢复原账号、密码和操作类型后重试。');return;}
    if(!pending){
      let saved=null;try{saved=JSON.parse(sessionStorage.getItem('ioa-auth-operation'));}catch{}
      if(saved&&(saved.action!==mode||saved.username!==username)){error('上次操作结果尚待核对，请先用原账号和操作类型重试。');return;}
      pending={fingerprint,id:saved&&saved.action===mode&&saved.username===username?saved.operationId:crypto.randomUUID()};
      try{sessionStorage.setItem('ioa-auth-operation',JSON.stringify({action:mode,username,operationId:pending.id}));}
      catch{pending=null;error('浏览器无法保存本次操作，请允许会话存储后重试。');return;}
    }
    busy=true;$('account-submit').disabled=true;$('auth-error').hidden=true;
    try{await WebSession.api('/api/auth/'+mode,{username,password,invite,operationId:pending.id});sessionStorage.removeItem('ioa-auth-operation');pending=null;$('account-password').value='';$('confirm-password').value='';location.replace(next);}
    catch(failure){if([400,401,403,422].includes(failure.status)){pending=null;sessionStorage.removeItem('ioa-auth-operation');}error(failure.message||'连接结果未知，请重试原操作。');}
    finally{busy=false;$('account-submit').disabled=mode==='register'&&config?.registration==='closed';}
  });
  $('admin-form').addEventListener('submit',async event=>{event.preventDefault();try{await WebSession.api('/api/admin/login',{username:$('admin-username').value,password:$('admin-password').value});$('admin-password').value='';location.replace('/admin');}catch(failure){error(failure.message);}});
  updateMode(mode);
  WebSession.api('/api/config').then(value=>{config=value;updateMode(mode);}).catch(()=>{});
  WebSession.ready.then(value=>{if(value)location.replace(value.role==='admin'&&!value.id?'/admin':next);});
})();
