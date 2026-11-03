// Execute the shipped inline script. These explicit DOM doubles do not emulate a browser.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(process.argv[2],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const decode=value=>value.replace(/&(amp|lt|gt|quot|#39);/g,(_,key)=>({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"}[key]));
class Element {
  constructor(tag,document){Object.assign(this,{tag,document,dataset:{},children:[],parent:null,listeners:{},value:'',disabled:false,textContent:'',renderCount:0})}
  set innerHTML(html){
    this.html=html;this.renderCount++;this.children=[];
    const pattern=/<(select|textarea|button|p)\b([^>]*\bdata-(?:answer|text|decision|decision-error)[^>]*)>([\s\S]*?)<\/\1>/g;
    for(const match of html.matchAll(pattern)){
      const child=new Element(match[1],this.document),attrs=match[2];
      for(const [,key,value] of attrs.matchAll(/data-([\w-]+)(?:="([^"]*)")?/g))child.dataset[key.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=decode(value||'');
      if(child.tag==='select'){
        child.multiple=/\bmultiple\b/.test(attrs);
        child.options=[...match[3].matchAll(/<option value="([^"]*)">/g)].map((option,index)=>({value:decode(option[1]),selected:index===0}));
      }
      this.appendChild(child);
    }
  }
  get innerHTML(){return this.html||''}
  get selectedOptions(){return this.options.filter(option=>option.selected)}
  matches(selector){
    if(selector==='.card')return this.className==='card';
    if(selector.startsWith('[')){
      const [,key,value]=selector.match(/^\[data-([\w-]+)(?:="([^"]*)")?\]$/);
      const actual=this.dataset[key.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())];
      return actual!==undefined&&(value===undefined||actual===value);
    }
    return selector.split(',').includes(this.tag);
  }
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)])}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null}
  closest(selector){return this.matches(selector)?this:this.parent?.closest(selector)||null}
  appendChild(node){return this.insertBefore(node,null)}
  insertBefore(node,before){
    if(node===before)return node;
    node.remove();const index=before?this.children.indexOf(before):this.children.length;
    assert(index>=0);this.children.splice(index,0,node);node.parent=this;return node;
  }
  remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null}}
  contains(node){return node===this||this.children.some(child=>child.contains(node))}
  get firstElementChild(){return this.children[0]||null}
  get nextElementSibling(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null}
  addEventListener(name,callback){this.listeners[name]=callback}
  focus(){this.document.activeElement=this}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end}
}
const tick=async()=>{for(let index=0;index<12;index++)await Promise.resolve()};
const question=(reference='question-1',bindingDigest='binding-1')=>({
  reference,bindingDigest,kind:'question',status:'pending',agentId:'test-agent',stage:'host.interaction',action:'native.answer',
  binding:{action:'native.answer',preview:{title:'真实显示的选择'}},expiresAt:'2099-01-01T00:00:00Z',
  questions:[{question:'单选',options:[{value:'yes',label:'同意'},{value:'no',label:'拒绝'}]},
    {question:'多选',multi_select:true,options:[{value:'a',label:'甲'},{value:'b',label:'乙'},{value:'c',label:'丙'}]},
    {question:'文字'}],
});
const approval=()=>({...question('approval','approval-binding'),kind:'approval',questions:[]});
const result=(item,status='answered')=>({...item,status});
async function setup(items){
  const document={activeElement:null,createElement(tag){return new Element(tag,this)}};
  const cards=new Element('main',document),error=new Element('p',document),requests=[],intervals=[],history=[];
  document.getElementById=id=>({cards,error}[id]);
  const context={console,document,location:{hash:'#one-time-entry'},history:{replaceState(...args){history.push(args)}},
    setInterval(callback,delay){intervals.push({callback,delay})},
    fetch(path,options){return new Promise((resolve,reject)=>requests.push({path,options,resolve,reject,done:false}))}};
  vm.createContext(context);vm.runInContext(script,context,{filename:'user.html'});
  async function reply(request,data,status=200){
    request.done=true;request.resolve({ok:status>=200&&status<300,status,json:async()=>data});await tick();
  }
  const pending=path=>requests.filter(request=>request.path===path&&!request.done);
  assert.equal(requests[0].path,'/user/session');
  assert.deepEqual(JSON.parse(requests[0].options.body),{token:'one-time-entry'});
  await reply(requests[0],{csrf:'browser-csrf'});
  await reply(pending('/user/api')[0],{csrf:'browser-csrf',confirmations:items});
  assert.equal(intervals.length,1);assert.equal(intervals[0].delay,1500);
  assert.equal(history[0][2],'/user');
  for(const request of requests)assert.equal(request.options.credentials,'same-origin');
  return {cards,error,requests,pending,reply,document,
    async poll(next){intervals[0].callback();await tick();const request=pending('/user/api')[0];assert(request);await reply(request,{csrf:'browser-csrf',confirmations:next})},
    async startPoll(){intervals[0].callback();await tick();return pending('/user/api')[0]},
    card(reference){return cards.children.find(card=>card.dataset.ref===reference)},
    async event(name,target){const returned=cards.listeners[name]({target});await tick();return returned},
    async click(card,decision='answer'){cards.listeners.click({target:card.querySelector(`[data-decision="${decision}"]`)});await tick()},
  };
}
function edit(h,card){
  const single=card.querySelector('[data-answer="0"]'),multi=card.querySelector('[data-answer="1"]');
  for(const option of single.options)option.selected=option.value==='yes';
  for(const option of multi.options)option.selected=['a','c'].includes(option.value);
  card.querySelector('[data-text="0"]').value='单选补充';card.querySelector('[data-text="1"]').value='多选补充';
  const text=card.querySelector('[data-text="2"]');text.value='仅文本 & <保留>';text.focus();text.setSelectionRange(2,4);
  h.cards.listeners.change({target:single});h.cards.listeners.input({target:text});
}
function answers(card){return [0,1,2].map(index=>({
  selected_options:card.querySelector(`[data-answer="${index}"]`)?.selectedOptions.map(option=>option.value).filter(Boolean)||[],
  custom_input:card.querySelector(`[data-text="${index}"]`).value,
}))}
const expected=[{selected_options:['yes'],custom_input:'单选补充'},
  {selected_options:['a','c'],custom_input:'多选补充'},{selected_options:[],custom_input:'仅文本 & <保留>'}];
function assertPost(h,item,decision='answer'){
  const request=h.pending('/user/decision')[0];assert(request);
  assert.equal(request.options.method,'POST');assert.equal(request.options.headers['X-Ioa-Csrf'],'browser-csrf');
  assert.equal(request.options.headers['Content-Type'],'application/json');assert.equal(request.options.credentials,'same-origin');
  assert.deepEqual(JSON.parse(request.options.body),{reference:item.reference,bindingDigest:item.bindingDigest,decision,...(decision==='answer'?{answers:expected}:{})});
  return request;
}
const scenarios={
  async closed(){
    const item={...question(),questions:[
      {question:'是否同意',allow_custom_input:false,options:[{value:'allow',label:'同意'},{value:'deny',label:'拒绝'}]},
      {question:'普通文本'}]};
    const h=await setup([item]),card=h.card(item.reference);
    assert.equal(card.querySelector('[data-text="0"]'),null);
    assert(card.querySelector('[data-answer="0"]'));assert(card.querySelector('[data-text="1"]'));
    card.querySelector('[data-text="1"]').value='普通问题仍可填';
    await h.click(card);
    const empty=h.pending('/user/decision')[0];
    assert.deepEqual(JSON.parse(empty.options.body).answers,[
      {selected_options:[],custom_input:''},{selected_options:[],custom_input:'普通问题仍可填'}]);
    await h.reply(empty,{error:{message:'请选择一个有效选项'}},400);
    await h.reply(h.pending('/user/api')[0],{csrf:'browser-csrf',confirmations:[item]});
    assert.equal(card.querySelector('[data-decision]').disabled,false);
    const choice=card.querySelector('[data-answer="0"]');
    for(const option of choice.options)option.selected=option.value==='allow';
    await h.event('change',choice);
    await h.poll([{...item,serverRevision:2}]);
    assert.equal(card.querySelector('[data-text="0"]'),null);
    assert.deepEqual(card.querySelector('[data-answer="0"]').selectedOptions.map(o=>o.value),['allow']);
    assert.equal(card.querySelector('[data-text="1"]').value,'普通问题仍可填');
    assert.equal(card.querySelector('[data-decision-error]').textContent,'请选择一个有效选项');
    await h.click(card);
    const corrected=h.pending('/user/decision')[0];
    assert.deepEqual(JSON.parse(corrected.options.body).answers,[
      {selected_options:['allow'],custom_input:''},{selected_options:[],custom_input:'普通问题仍可填'}]);
    await h.reply(corrected,result(item));
    assert(card.innerHTML.includes('已答复'));assert.equal(card.querySelector('[data-decision]'),null);
    assert.equal(card.querySelectorAll('select,textarea').length,0);
  },
  async drafts(){
    const item=question(),h=await setup([item]),card=h.card(item.reference);edit(h,card);
    const text=card.querySelector('[data-text="2"]'),renderCount=card.renderCount;
    await h.poll([approval(),item]);
    assert.equal(h.card(item.reference),card);assert.equal(card.renderCount,renderCount);assert.deepEqual(answers(card),expected);
    assert.equal(h.document.activeElement,text);assert.deepEqual([text.selectionStart,text.selectionEnd],[2,4]);
    await h.poll([{...item,serverRevision:2},approval()]);
    assert.deepEqual(answers(card),expected);assert.equal(h.cards.firstElementChild,card);
    assert.equal(h.pending('/user/decision').length,0);
  },
  async success(){
    const item=question(),h=await setup([item]),card=h.card(item.reference);edit(h,card);
    const delayed=await h.startPoll(),renderCount=card.renderCount;
    await h.click(card);const post=assertPost(h,item);await h.click(card);
    assert.equal(h.requests.filter(request=>request.path==='/user/decision').length,1);
    assert(card.querySelectorAll('select,textarea,button').every(input=>input.disabled));
    await h.reply(delayed,{csrf:'browser-csrf',confirmations:[{...item,serverRevision:3}]});
    assert.equal(card.renderCount,renderCount);assert.deepEqual(answers(card),expected);
    const beforeDecision=await h.startPoll();
    await h.reply(post,result(item));
    assert(card.innerHTML.includes('已答复'));assert.equal(card.querySelector('[data-decision]'),null);
    await h.reply(beforeDecision,{csrf:'browser-csrf',confirmations:[item]});
    assert(card.innerHTML.includes('已答复'));assert.equal(card.querySelector('[data-decision]'),null);
    const followup=h.pending('/user/api')[0];assert(followup);
    await h.reply(followup,{csrf:'browser-csrf',confirmations:[item]});
    assert(card.innerHTML.includes('已答复'));assert.equal(card.querySelectorAll('select,textarea').length,0);
    assert.equal(h.requests.filter(request=>request.path==='/user/decision').length,1);
    const other=approval();await h.poll([other]);await h.click(h.card(other.reference),'approve');
    await h.reply(assertPost(h,other,'approve'),result(other,'approved'));
    assert(h.card(other.reference).innerHTML.includes('已允许'));
  },
  async failure(){
    const item=question(),h=await setup([item]),card=h.card(item.reference);edit(h,card);await h.click(card);
    await h.reply(assertPost(h,item),{error:{message:'答案未被接受，请修改'}},409);
    assert.deepEqual(answers(card),expected);assert.equal(card.querySelector('[data-decision-error]').textContent,'答案未被接受，请修改');
    await h.reply(h.pending('/user/api')[0],{csrf:'browser-csrf',confirmations:[{...item,serverRevision:2}]});
    assert.deepEqual(answers(card),expected);assert.equal(card.querySelector('[data-decision-error]').textContent,'答案未被接受，请修改');
    assert.equal(card.querySelector('[data-decision]').disabled,false);
    card.querySelector('[data-text="2"]').value='修正后的回答';await h.click(card);
    const retry=h.pending('/user/decision')[0];assert.equal(JSON.parse(retry.options.body).answers[2].custom_input,'修正后的回答');
    assert.equal(card.querySelector('[data-decision-error]').textContent,'');
    await h.reply(retry,result(item));assert(card.innerHTML.includes('已答复'));
  },
  async unknown(){
    const item=question(),h=await setup([item]),card=h.card(item.reference);edit(h,card);await h.click(card);
    const post=assertPost(h,item);post.done=true;post.reject(Error('连接中断'));await tick();
    assert(card.querySelector('[data-decision-error]').textContent.includes('不会自动重发'));
    assert(card.querySelector('[data-decision]').disabled);await h.click(card);
    assert.equal(h.requests.filter(request=>request.path==='/user/decision').length,1);
    const check=h.pending('/user/api')[0];assert(check);
    await h.reply(check,{csrf:'browser-csrf',confirmations:[result(item)]});
    assert(card.innerHTML.includes('已答复'));assert.equal(card.querySelector('[data-decision-error]').textContent,'');
    assert.equal(h.requests.filter(request=>request.path==='/user/decision').length,1);
  },
  async binding(){
    const item=question(),h=await setup([item]),old=h.card(item.reference);edit(h,old);
    const changed=question(item.reference,'replacement-binding');await h.poll([changed]);
    const fresh=h.card(item.reference);assert.notEqual(fresh,old);
    assert.deepEqual(answers(fresh),[0,1,2].map(()=>({selected_options:[],custom_input:''})));
    edit(h,fresh);await h.poll([result(changed,'expired')]);
    assert.equal(fresh.querySelector('[data-decision]'),null);assert.equal(fresh.querySelectorAll('select,textarea').length,0);
    await h.poll([changed]);assert(fresh.innerHTML.includes('已过期'));assert.equal(fresh.querySelector('[data-decision]'),null);
    await h.poll([]);assert.equal(h.cards.children.length,1);assert.equal(h.cards.children[0].textContent,'当前没有待确认操作。');
  },
};
(async()=>{await scenarios[process.argv[3]]();console.log(`owner-view ${process.argv[3]} passed (Node VM DOM/network doubles)`);})().catch(error=>{console.error(error);process.exitCode=1});
