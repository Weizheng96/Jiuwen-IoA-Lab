// Check external participant mapping and signed-evidence rendering without a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const context=vm.createContext({document:{addEventListener(){}},console});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/platform/ioalab/web/static/common.js'),'utf8')+'\nthis.exports={Names,Cases,Focus};',context);
const {Names,Cases,Focus}=context.exports;
const client='urn:a2c:client:external';
Names.seedUsers([{id:'u-one',username:'one',agents:[{id:client,role:'assistant',name:'Swarm'}]}]);
Names.seedServices([{id:'service',name:'Service',owner_id:'u-one',external:true,url:'https://provider.example/a2a'}]);
const cases=new Cases();
const {c}=cases.ingest({op:'order.completed',sender:client,cardDigest:'sha256:example',detail:{
  provider:'https://provider.example/a2a',evidenceTrust:'verified',state:'started',periods:1,total:'10',
  signatures:{1:true,2:true,3:true,4:[true],5:[true],6:[],7:[]}}});
assert.equal(c.assistant,client);assert.equal(c.provider,'https://provider.example/a2a');
assert.equal(c.state,'进行中');assert.equal(c.sigs[7].length,0);
assert.equal(Focus.accountsOfCase(c).join(','),'u-one');
// The resident path never infers success from a queued method name.
const real=new Cases(),cd='sha256:resident';
let item=real.ingest({op:'delivery.anchor',sender:client,cardDigest:cd,outcome:'queued',detail:{source:'resident'}}).c;
assert.equal(item.sigs[5].length,0);
const signed={1:true,2:true,3:true,4:[true],5:[true],6:[true],7:[true]};
real.ingest({op:'card.close',sender:client,cardDigest:cd,outcome:'completed',detail:{source:'resident',evidenceTrust:'verified',state:'completed',periods:1,total:'9',signatures:signed}});
assert.equal(item.state,'已结清');
real.ingest({op:'work.start.result',sender:client,cardDigest:cd,outcome:'working',detail:{source:'resident',evidenceTrust:'verified',state:'started',periods:1,total:'9',signatures:{1:true,2:true,3:true,4:[],5:[],6:[],7:[]}}});
assert.equal(item.state,'已结清');assert.equal(item.sigs[7].length,1);assert.equal(item.phase,5);assert.equal(item.periodMax,1);
console.log('External identities, account focus, resident timing and signed evidence passed');

const staticRoot=path.join(__dirname,'../src/platform/ioalab/web/static'),crypto=require('node:crypto');
for(const name of ['index.html','admin.html']) {
  const html=fs.readFileSync(path.join(staticRoot,name),'utf8');
  for(const match of html.matchAll(/\/static\/([A-Za-z0-9_-]+\.(?:css|js))(?:\?sha256=([a-f0-9]{16}))?(?=["'])/g)) {
    const content=fs.readFileSync(path.join(staticRoot,match[1]),'utf8').replace(/\r\n/g,'\n');
    assert.equal(match[2],crypto.createHash('sha256').update(content).digest('hex').slice(0,16),'Run tools/stamp_static.py');
  }
}
console.log('Static content digests passed');
