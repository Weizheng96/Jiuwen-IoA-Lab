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
console.log('External identities, account focus and signed evidence passed');
