'use strict';
for(const [id,path,name] of [['enter','/api/trial/entry','ioa-native'],['confirm','/api/trial/confirmation-entry','ioa-confirm']]){
  document.getElementById(id).onclick=async function(){
    this.disabled=true;
    const page=window.open('about:blank',name);if(page)page.opener=null;
    try{const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});const data=await response.json();if(!response.ok)throw Error(data.error?.message||'试用入口暂不可用');
      if(page){page.location.replace(data.url);document.getElementById('status').textContent='已打开独立页面。获得席位后，可在此打开可信确认页。';}
      else{throw Error('请允许本站打开新页面，再重试。');}}
    catch(error){if(page)page.close();document.getElementById('status').textContent=error.message;}
    finally{this.disabled=false;}
  };
}
