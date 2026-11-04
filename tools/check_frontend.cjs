// Browser-independent checks of operation recovery and redirects in the shipped JS.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.join(__dirname,'../src/platform/ioalab/web/static');
function authPage(storage=new Map()) {
  const elements=new Map(),calls=[],redirects=[];
  function element(id){if(!elements.has(id))elements.set(id,{value:'',hidden:false,disabled:false,events:{},setAttribute(){},addEventListener(name,fn){this.events[name]=fn;}});return elements.get(id);}
  let response=()=>Promise.reject(Error('reply lost'));
  const context={URLSearchParams,crypto:{randomUUID:require('node:crypto').randomUUID},
    document:{getElementById:element},history:{replaceState(){}},location:{pathname:'/login',search:'?next=%2Fassistants%23friend',replace(value){redirects.push(value);}},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    WebSession:{safeNext:value=>value,ready:Promise.resolve(null),api:async(route,body)=>{if(route==='/api/config')return {registration:'open'};calls.push({route,body});return response();}}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'auth-page.js'),'utf8'),context);
  element('account-name').value='alice';element('account-password').value='a-local-test-password';
  return {element,calls,redirects,storage,respond:value=>response=value,submit:()=>element('account-form').events.submit({preventDefault(){}})};
}
(async()=>{
  const page=authPage();await page.submit();assert.equal(page.calls.length,1);
  const operation=page.calls[0].body.operationId;
  const stored=JSON.parse(page.storage.get('ioa-auth-operation'));
  assert.equal(stored.operationId,operation);assert.deepEqual(Object.keys(stored).sort(),['action','operationId','username']);
  await page.submit();assert.equal(page.calls[1].body.operationId,operation);
  page.element('account-password').value='changed-password';await page.submit();assert.equal(page.calls.length,2,'unknown result cannot accept changed credentials');
  const restored=authPage(page.storage);await restored.submit();assert.equal(restored.calls[0].body.operationId,operation,'reload must reuse the original ID');
  restored.respond(()=>Promise.reject(Object.assign(Error('conflict needs reconciliation'),{status:409})));
  await restored.submit();assert.equal(restored.calls.at(-1).body.operationId,operation);assert.ok(restored.storage.has('ioa-auth-operation'));
  restored.respond(()=>Promise.resolve({token:'not-persisted'}));await restored.submit();assert.deepEqual(restored.redirects,['/assistants#friend']);assert.equal(restored.storage.size,0);
  const rejected=authPage();rejected.respond(()=>Promise.reject(Object.assign(Error('wrong password'),{status:400})));await rejected.submit();const old=rejected.calls[0].body.operationId;
  rejected.element('account-password').value='corrected-password';rejected.respond(()=>Promise.resolve({}));await rejected.submit();assert.notEqual(rejected.calls[1].body.operationId,old);
  const blocked=authPage();blocked.storage.set=()=>{throw Error('storage disabled');};await blocked.submit();assert.equal(blocked.calls.length,0,'do not submit an operation without recoverable metadata');
  console.log('Frontend unknown-result recovery, reload, credential changes and definite rejection passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
