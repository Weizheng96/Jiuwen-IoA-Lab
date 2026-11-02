"""Execute real workbench renderers with an explicit in-memory DOM driver."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'),reason='Node is required for supplementary JavaScript rendering checks')
def test_candidate_history_grouping_and_explicit_selection():
    static=Path(__file__).resolve().parents[1]/'src/platform/ioalab/web/static'
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const nodes=new Map();
function node(id){
  if(!nodes.has(id))nodes.set(id,{
    innerHTML:'',textContent:'',dataset:{},children:[{}],style:{},
    classList:{add(){},remove(){},toggle(){}},
    querySelector(s){return node(id+'/'+s);},querySelectorAll(){return [];},replaceChildren(){},
  });
  return nodes.get(id);
}
const context={console,document:{getElementById:node,querySelectorAll(){return [];},addEventListener(){},body:node('body')},
  location:{origin:'http://platform.example'},setInterval(){},requestAnimationFrame(){},
  fetch:async(path)=>({ok:true,json:async()=>path==='/api/session'?{id:null}:[]}),
  NetworkMap:{components:[],subs:[],random:()=>()=>.5}};
context.window=context;vm.createContext(context);
for(const file of ['network.js','provider-workbench.js','index.js'])vm.runInContext(fs.readFileSync(process.argv[1]+'/'+file,'utf8'),context,{filename:file});
const order=(id,effective,state)=>({card_digest:id,effective,state,providerName:id,clientName:'测试助手',provider_id:id,
  phase:state==='unformed-closed'?'未成约 · 已结束':state,total:'299',paid:'0.00',proposalFeePaid:'1.00',created:1,signatures:{}});
const closed=order('closed-candidate',false,'unformed-closed'),candidate=order('active-candidate',false,'offered'),
  current=order('active-contract',true,'signed'),history=order('finished-contract',true,'completed');
const orders=[closed,candidate,current,history];
context.fixture={account:{id:'owner',username:'测试用户'},roles:[],wallet:'1000',identities:[],messages:[],interfaces:[],
  events:[],intents:[],publicServices:[],orders,categories:[],services:[{id:'service',name:'测试服务',url:'service',status:'online',
    activeDelegations:1,completedCount:1,earned:'2.00',commissionEarned:'0.00',proposalFeesEarned:'2.00',orders,intents:[]}]};
vm.runInContext('data=fixture;render()',context);
assert(node('proposal-orders').innerHTML.includes('closed-candidate'));
assert(node('proposal-orders').innerHTML.includes('active-candidate'));
assert(!node('proposal-orders').innerHTML.includes('active-contract'));
assert(node('orders').innerHTML.includes('active-contract'));
assert(!node('orders').innerHTML.includes('closed-candidate'));
assert(node('order-history').innerHTML.includes('finished-contract'));
assert(!node('order-history').innerHTML.includes('closed-candidate'));
assert.equal(node('journey-status').textContent,'候选合同 · active-candidate');
assert(node('journey-evidence').innerHTML.includes('候选报价'));
assert(!node('journey-evidence').innerHTML.includes('合同总额'));
const provider=node('services').innerHTML;
const workspace=provider.split('<h3>履约工作区</h3>')[1].split('<h3>候选合同记录</h3>')[0];
assert(workspace.includes('active-contract')&&!workspace.includes('closed-candidate'));
const proposals=provider.split('<h3>候选合同记录</h3>')[1].split('<h3>完成与历史</h3>')[0];
assert(proposals.includes('closed-candidate')&&proposals.includes('active-candidate'));
assert(!proposals.includes('active-contract')&&proposals.includes('查看候选合同'));
assert(!proposals.includes('合同总额'));
context.IoaNetwork.chooseOrder('closed-candidate');
context.IoaNetwork.render(context.fixture);
assert.equal(node('journey-status').textContent,'未成约 · 已结束 · closed-candidate');
assert.equal(node('ownService/small').textContent,'未成约 · 已结束');
context.IoaNetwork.chooseOrder('active-contract');
assert.equal(node('journey-status').textContent,'当前委托 · active-contract');
assert(node('journey-evidence').innerHTML.includes('合同总额'));
'''
    result=subprocess.run([shutil.which('node'),'-e',script,str(static)],capture_output=True,text=True,encoding='utf-8',timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
