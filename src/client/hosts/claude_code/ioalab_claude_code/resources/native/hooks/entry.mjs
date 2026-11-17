import {loadBindings,descriptor,invoke,post} from '../wrapper.mjs';
import {Correlation,ToolCalls,ReadonlyCalls} from './causality.mjs';

const generation=crypto.randomUUID();
let inFlight=false;
let current=null;
const approvedBindings=new Map();
let timerStarted=false;
const helloBoots=new Map();
const readonlyCalls=new ReadonlyCalls();
let observationInFlight=false;
const pluginName='ioa-native-client';
let evidenceQueue=Promise.resolve();

async function publish($,job=current) {
  if(!job || current!==job)return;
  for(const turnId of job.correlation.turns.keys()) {
    const binding=job.correlation.binding(turnId);
    if(!binding.linked)continue;
    const grant=await descriptor($,job.binding);
    if(grant.bootId!==job.bootId){job.correlation.invalidate();return;}
    if(!job.requestId) {
      await exchange($,grant,{...job.identity,kind:'reference',requestId:turnId,proof:binding.proof});
      job.requestId=turnId;
    }
    if(job.requestId!==turnId){job.correlation.invalidate();return;}
    const fact=job.correlation.assessment(turnId);
    if(fact.linked) {
      await exchange($,grant,{...job.identity,kind:'terminal',requestId:turnId,proof:fact.proof,
        state:fact.terminal,coreComplete:true,output:job.outputs.get(turnId)??null});
      current=null;
    }
  }
}
function scheduleEvidence($,job=current) {
  // Losing a transport event invalidates evidence; it never resubmits a prompt.
  evidenceQueue=evidenceQueue.then(()=>publish($,job)).catch(()=>{job?.correlation.invalidate();});
  return evidenceQueue;
}

async function exchange($,grant,body) {
  const result=await post($,grant.controlUrl,grant.grant,grant.bootId,{...body,
    sessionId:await $.session.id(),generation});
  if(result.status!=='completed')throw Error('native_control_unavailable');
  return result;
}
async function poll($) {
  if(inFlight || !approvedBindings.size)return;
  inFlight=true;
  try {
    for(const binding of approvedBindings.values()) {
      try {
        const grant=await descriptor($,binding);
        if(helloBoots.get(grant.profile)!==grant.bootId) {
          await exchange($,grant,{kind:'hello',workspace:await $.session.cwd()});
          helloBoots.set(grant.profile,grant.bootId);
        }
        if(current)continue; // A new SDK boot refreshes transport, never replays the old operation.
        const response=await exchange($,grant,{kind:'poll'});
        const item=response.data?.item;
        if(!item)continue;
        const identity={operationId:item.operationId,attemptId:item.attemptId};
        const version=await $.session.version();
        const job={identity,binding,bootId:grant.bootId,outputs:new Map(),requestId:null,calls:new ToolCalls(),
          correlation:new Correlation({sessionId:await $.session.id(),pluginName,
            engineBuild:version.base??version.version,observerGeneration:generation,
            attemptKey:item.attemptId,body:item.text})};
        current=job;
        try {
          const result=await $.prompt.submit({text:item.text});
          if(current===job)await exchange($,grant,{...identity,kind:typeof result?.drop==='string'?'uncertain':'accepted'});
        } catch {
          job.correlation.invalidate();
          try {await exchange($,grant,{...identity,kind:'uncertain'});}catch{}
        }
      } catch {} // One missing role entry never authorizes polling a different role's work.
    }
  } catch {} // Missing entry/binding is reported by the public invocation; no fallback work.
  finally {inFlight=false;}
}
async function pollObservations($) {
  if(observationInFlight || !approvedBindings.size)return;
  observationInFlight=true;
  try {
    for(const binding of approvedBindings.values()) {
      try {
        const grant=await descriptor($,binding);
        if(helloBoots.get(grant.profile)!==grant.bootId)continue;
        const response=await exchange($,grant,{kind:'poll_observation'});
        if(response.data?.item)await readonlyCalls.run($,response.data.item,grant,async body=>{
          const fresh=await descriptor($,binding);
          if(fresh.bootId!==grant.bootId || fresh.grant!==grant.grant)throw Error('Original observer generation changed');
          return (await exchange($,fresh,body)).data;
        });
      }catch{} // A lost observation never creates another API/chat request.
    }
  } finally {observationInFlight=false;}
}
function startPolling($) {
  if(!timerStarted && approvedBindings.size){$.clock.every(1000,()=>{
    void poll($);void pollObservations($);
  });timerStarted=true;}
}
export function register(on) {
  on('prompt.submit',async($,e,next)=>{
    const job=current;
    const result=await next(e);
    try {if(job && e.origin?.kind==='plugin' && e.origin.name===pluginName && e.text===job.correlation.body){
      await job.correlation.prompt(e,result);await scheduleEvidence($,job);
    }}catch{job?.correlation.invalidate();}
    return result;
  });
  on('session.append',async($,e,next)=>{
    const job=current;
    const result=await next(e);
    try {if(job){await job.correlation.append(e,result,next.origin);await scheduleEvidence($,job);}}
    catch{job?.correlation.invalidate();}
    return result;
  });
  on('turn.start',async($,e,next)=>{
    const job=current;
    try {if(job){await job.correlation.start(e,next.origin);await scheduleEvidence($,job);}}
    catch{job?.correlation.invalidate();}
    return next(e);
  });
  on('turn.complete',async($,e,next)=>{
    const job=current;
    const result=await next(e);
    try {if(job){job.correlation.complete(e,next.origin);job.outputs.set(e.turnId,result?.text??null);
      await scheduleEvidence($,job);}}
    catch{job?.correlation.invalidate();}
    return result;
  });
  on('turn.step',async function*($,e,next){
    const job=current;
    const downstream=next(e);
    let item=await downstream.next();
    try {
      while(!item.done) {
        try{job?.calls.chunk(e,item.value,next.origin);}catch{job?.correlation.invalidate();}
        let input;
        try{input=yield item.value;}
        catch(error){if(!downstream.throw)throw error;item=await downstream.throw(error);continue;}
        item=await downstream.next(input);
      }
      try{job?.calls.settle(e,next.origin);}catch{job?.correlation.invalidate();}
      return item.value;
    } finally {if(!item.done && downstream.return)await downstream.return();}
  });
  on('tool.call',{tool:'mcp__ioa-native-client__sdk_tool'},async($,e)=>{
    const job=current;
    try {
      if(!job || !job.requestId)throw Error('Native SDK request binding is missing');
      const proof=job.correlation.binding(job.requestId);
      if(!proof.linked)throw Error('Native SDK request evidence is missing');
      const args={name:e.name,arguments:e.arguments};
      const toolProof=await job.calls.proof(e.tool_use_id,e.tool,args,job.requestId,generation);
      const grant=await descriptor($,job.binding);
      if(grant.bootId!==job.bootId)throw Error('Original SDK generation changed');
      const result=await exchange($,grant,{...job.identity,kind:'tool',requestId:job.requestId,
        proof:proof.proof,callId:e.tool_use_id,name:e.name,arguments:e.arguments,toolProof});
      return {result:result.data?.toolResult};
    } catch {return {result:{error:{code:'unknown_result',phase:'native_sdk_tool',
      action:'Inspect the original SDK request/tool receipt; do not repeat the effect'}}};}
  });
  on('session.start',async($,e,next)=>{
    try {
      await $.tool.register({name:'sdk_tool',description:'Call a tool from the SDK-frozen execution definition. Original request and tool call are verified automatically.',
        inputSchema:{type:'object',properties:{name:{type:'string'},arguments:{type:'object'}},
          required:['name','arguments'],additionalProperties:false}});
      // Entry tools exist even when a descriptor/client is missing, so callers
      // receive the actual missing-capability action instead of silent absence.
      for(const profile of ['assistant','provider']) {
        await $.tool.register({name:'client_'+profile,description:'Call the bound public IoA '+profile+' client. Uses actual native source automatically.',
          inputSchema:{type:'object',properties:{operation:{type:'string'},operationId:{type:'string'},arguments:{type:'object'}},
            required:['operation','arguments'],additionalProperties:false}});
      }
      const bindings=await loadBindings($);
      for(const profile of ['assistant','provider']) {
        const binding=bindings.profiles?.[profile];
        if(!binding)continue;
        approvedBindings.set(profile,binding);
        try {
          const grant=await descriptor($,binding);
          await exchange($,grant,{kind:'hello',workspace:await $.session.cwd()});
          helloBoots.set(grant.profile,grant.bootId);
        } catch {}
      }
      startPolling($);
    } catch {} // An unavailable optional observation must not prevent native startup.
    return next(e);
  });
  for(const profile of ['assistant','provider']) {
    on('tool.call',{tool:'mcp__ioa-native-client__client_'+profile},async($,e)=>{
      const operationId=e.operationId??crypto.randomUUID();
      try {
        const bindings=await loadBindings($);
        const binding=bindings.profiles?.[profile];
        if(!binding)throw Error('native_entry_binding_missing');
        // Fixed observer provenance, never a model-supplied tool field.
        binding.observerGeneration=generation;
        const result=await invoke($,binding,{operation:e.operation,operationId,arguments:e.arguments},async grant=>{
          await exchange($,grant,{kind:'hello',workspace:await $.session.cwd()});
          helloBoots.set(grant.profile,grant.bootId);
          approvedBindings.set(profile,binding);startPolling($);
        });
        return {result};
      } catch(error) {return {result:error?.submissionUnknown?{status:'unknown',operationId,
        error:{code:'unknown_result',phase:'public_call',action:'Query the original operation; do not resend or change its ID'}}:
        {status:'rejected',operationId,error:{code:'not_ready',phase:'native_entry',
        action:'Install the complete IoA entry resources, start the bound client and reload this plugin in the original session'}}};}
    });
  }
}
