// Pure metadata boundary. No timers, submission, tools, state machine or native startup.
// An accepted frame profile must come from a reviewed public build contract, not observation alone.
export async function sha256(text) {
  const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)));
  return Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
}

export function restrictedOrigin(value,pluginName) {
  return {kind:value?.kind==='plugin'?'plugin':value?.kind===undefined?'missing':'other',
    nameMatches:value?.kind==='plugin' && value.name===pluginName,
    name:value?.kind==='plugin' && value.name===pluginName?pluginName:null,
    asUser:value?.asUser===true,asUserPresent:value?.asUser!==undefined};
}

function ours(value,pluginName) {
  const origin=restrictedOrigin(value,pluginName);
  return origin.kind==='plugin' && origin.nameMatches && !origin.asUserPresent;
}

function singleText(message) {
  return message?.type==='user' && message.role==='user' && Array.isArray(message?.content) && message.content.length===1 &&
    message.content[0]?.type==='text' && typeof message.content[0].text==='string'
      ? message.content[0].text : null;
}

function core(caller) { return caller?.plugin==='engine' && caller?.tier==='core'; }

export class Correlation {
  constructor({sessionId,pluginName,engineBuild,observerGeneration,attemptKey,body,frameProfile=null}) {
    if(![sessionId,pluginName,engineBuild,observerGeneration,attemptKey,body].every(x=>typeof x==='string' && x.length))
      throw Error('Missing SDK-bound private resource');
    this.scope={sessionId,pluginName,engineBuild,observerGeneration,attemptKey};
    this.body=body; // In memory only; callers persist digests, never this instance.
    // No accepted frame profile is shipped: a raw input row is required.
    if(frameProfile!==null)throw Error('Native frame profile has not been validated');
    this.profile=null;
    this.promptVerified=false;
    this.rows=new Map();
    this.turns=new Map();
    this.terminals=new Map();
    this.invalid=false;
    this.rowFacts=new Map();
  }

  invalidate() { this.invalid=true; }

  async prompt(input,result) {
    const before=restrictedOrigin(input?.origin,this.scope.pluginName);
    const after=restrictedOrigin(result?.origin,this.scope.pluginName);
    const inputMatch=input?.text===this.body;
    const resultMatch=result?.text===this.body;
    this.promptVerified=!this.invalid && inputMatch && resultMatch && input?.turnId===undefined &&
      ours(input?.origin,this.scope.pluginName) && ours(result?.origin,this.scope.pluginName) && !result?.drop;
    return {event:'prompt.submit.observed',...this.scope,inputOrigin:before,resultOrigin:after,
      inputMatch,resultMatch,bodySha256:await sha256(this.body),verified:this.promptVerified,
      dropped:typeof result?.drop==='string'};
  }

  async frame(text) {
    const hash=await sha256(text);
    const pieces=text.split(this.body);
    const diagnostic={sha256:hash,length:text.length,bodyOccurrences:pieces.length-1};
    if(pieces.length===2) Object.assign(diagnostic,{prefixLength:pieces[0].length,prefixSha256:await sha256(pieces[0]),
      suffixLength:pieces[1].length,suffixSha256:await sha256(pieces[1])});
    const p=this.profile;
    const approved=!!p && p.verifiedByPublicContract===true && p.engineBuild===this.scope.engineBuild &&
      typeof p.approvalRef==='string' && p.approvalRef.length>0;
    if(!approved) return {...diagnostic,verified:false,reason:'frame_profile_unverified'};
    if(!Number.isSafeInteger(p.prefixLength) || p.prefixLength<0 || !Number.isSafeInteger(p.suffixLength) || p.suffixLength<0)
      return {...diagnostic,verified:false,reason:'frame_profile_invalid'};
    const prefix=text.slice(0,p.prefixLength);
    const suffix=p.suffixLength?text.slice(-p.suffixLength):'';
    const middle=text.slice(p.prefixLength,p.suffixLength?-p.suffixLength:undefined);
    const verified=text.length===p.prefixLength+this.body.length+p.suffixLength && middle===this.body &&
      await sha256(prefix)===p.prefixSha256 && await sha256(suffix)===p.suffixSha256;
    return {...diagnostic,verified,reason:verified?'exact_profile':'frame_mismatch'};
  }

  async append(input,result,caller) {
    const inputText=singleText(input?.message);
    const text=singleText(result?.message);
    const identity=core(caller) && !input?.agentId && input?.uuid===result?.uuid && typeof input?.uuid==='string' &&
      ours(input?.origin,this.scope.pluginName) && ['prompt','queue'].includes(input?.door);
    const frame=text===null?{verified:false,reason:'single_text_row_required'}:await this.frame(text);
    // If the public append input exposes the exact raw logical body, its pinned UUID
    // bridges to the stored row across native rewrites without guessing the wrapper.
    // If input is already framed, only a reviewed exact frame profile may bridge it.
    const rawInputMatch=inputText===this.body;
    const verified=!this.invalid && identity && text!==null && (rawInputMatch || frame.verified);
    if(verified && input.door==='prompt') {
      const matches=this.rows.get(frame.sha256)??new Set();
      matches.add(input.uuid);
      this.rows.set(frame.sha256,matches);
      this.rowFacts.set(input.uuid,{rowUuid:input.uuid,inputSha256:await sha256(inputText),storedSha256:frame.sha256,
        rawInputMatch,originVerified:ours(input.origin,this.scope.pluginName),uuidPinned:true,core:true});
    }
    // A queue row UUID is useful evidence, but is never guessed to be a prompt UUID/turn ID.
    return {event:'session.append.observed',...this.scope,door:['prompt','queue'].includes(input?.door)?input.door:'other',
      rowUuid:identity?input.uuid:null,origin:restrictedOrigin(input?.origin,this.scope.pluginName),
      rawInputMatch,inputTextSha256:inputText===null?null:await sha256(inputText),frame,verified};
  }

  async start(input,caller) {
    const frame=typeof input?.text==='string'?await this.frame(input.text):{verified:false,reason:'text_missing'};
    const validCore=!this.invalid && core(caller) && !input?.agentId && typeof input?.text==='string' &&
      typeof input?.turnId==='string' && input.turnId.length>0;
    if(validCore) this.turns.set(input.turnId,frame.sha256);
    return {event:'turn.start.observed',...this.scope,turnId:core(caller)?input?.turnId:null,
      frame,matchedStoredRow:!!validCore && this.rows.get(frame.sha256)?.size===1};
  }

  complete(input,caller) {
    const terminal={answer:'completed',error:'failed',refusal:'failed',aborted:'cancelled'}[input?.reason]??null;
    const valid=core(caller) && !input?.agentId && typeof input?.turnId==='string' && !!terminal &&
      input.isAborted===(input.reason==='aborted');
    if(valid) {
      if(this.terminals.has(input.turnId) && this.terminals.get(input.turnId)!==terminal) this.invalid=true;
      else this.terminals.set(input.turnId,terminal);
    }
    return {event:'turn.complete.observed',...this.scope,...this.assessment(input?.turnId)};
  }

  assessment(turnId) {
    const binding=this.binding(turnId);
    const linked=binding.linked && this.terminals.has(turnId);
    return {...binding,linked:!!linked,terminal:linked?this.terminals.get(turnId):null};
  }

  binding(turnId) {
    const textSha256=this.turns.get(turnId);
    const rowIds=this.rows.get(textSha256);
    const sameTextTurns=[...this.turns.values()].filter(hash=>hash===textSha256);
    const linked=!this.invalid && this.promptVerified && sameTextTurns.length===1 && rowIds?.size===1;
    const row=linked?this.rowFacts.get([...rowIds][0]):null;
    return {turnId,linked:!!linked,proof:row?{...row,turnSha256:textSha256,promptVerified:true,
      uniqueTurn:true,engineBuild:this.scope.engineBuild,observerGeneration:this.scope.observerGeneration}:null};
  }
}

function canonical(value) {
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value!==null && typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  return JSON.stringify(value);
}

// Only actual core stream chunks populate this table. Tool arguments cannot
// choose call IDs, turn IDs or the source role. It is in-memory transport data.
export class ToolCalls {
  constructor(){this.blocks=new Map();this.calls=new Map();this.invalid=false;}
  chunk(event,chunk,caller) {
    if(this.invalid || !core(caller) || event?.agentId!==undefined || typeof event?.turnId!=='string')return;
    const key=event.turnId+':'+event.index+':'+chunk?.index;
    if(chunk?.kind==='tool') {
      if(typeof chunk.id!=='string' || typeof chunk.name!=='string' || this.calls.has(chunk.id)){this.invalid=true;return;}
      const call={turnId:event.turnId,callId:chunk.id,nativeName:chunk.name,json:'',settled:false};
      this.blocks.set(key,call);this.calls.set(chunk.id,call);
    } else if(chunk?.kind==='input') {
      const call=this.blocks.get(key);
      if(call && typeof chunk.json==='string') {
        call.json+=chunk.json;
        if(call.json.length>1048576)this.invalid=true;
      }
    }
  }
  settle(event,caller) {
    if(!core(caller) || event?.agentId!==undefined)return;
    for(const [key,call] of this.blocks)if(key.startsWith(event.turnId+':'+event.index+':')) {
      try{call.arguments=JSON.parse(call.json);call.settled=true;}catch{call.settled=false;}
    }
  }
  async proof(callId,nativeName,args,turnId,generation) {
    const call=this.calls.get(callId);
    if(this.invalid || !call?.settled || call.turnId!==turnId || call.nativeName!==nativeName ||
        canonical(call.arguments)!==canonical(args))throw Error('Native tool call is not proven');
    return {callId,requestId:turnId,nativeName,argumentsSha256:await sha256(canonical(args)),
      observerGeneration:generation,core:true};
  }
}

// 197 isolated readonly API invocation, independent of chat-turn evidence.
export class ReadonlyCalls {
  constructor(){this.calls=new Map();}
  run($,job,scope,publish) {
    const key=job.operationId+':'+job.attemptId;
    const fingerprint=JSON.stringify([job.operationId,job.attemptId,job.scopeDigest,job.actor,job.profile,job.hostRef,
      job.sessionId,job.inputSha256,job.prompt,job.instructions,scope.bootId,scope.actor,scope.profile,scope.hostRef,
      scope.installationRef,scope.accountBindingRef]);
    const old=this.calls.get(key);
    if(old) {
      if(old.fingerprint!==fingerprint)
        throw Error('Original readonly invocation changed');
      return old.promise; // Same in-process invocation, never another API call.
    }
    const record={fingerprint,inputSha256:job.inputSha256,sessionId:job.sessionId,bootId:scope.bootId};
    // Insert before the first await. This UUID is a receipt locator, not proof.
    record.reference='claude-completion:'+crypto.randomUUID();
    this.calls.set(key,record);
    record.promise=this.execute($,job,scope,publish,record.reference);
    return record.promise;
  }
  async execute($,job,scope,publish,reference) {
    if(!scope.bootId || job.actor!==scope.actor || job.profile!==scope.profile || job.hostRef!==scope.hostRef ||
        await $.session.id()!==job.sessionId || await sha256(job.prompt)!==job.inputSha256)
      throw Error('Readonly native scope mismatch');
    const identity={operationId:job.operationId,attemptId:job.attemptId,scopeDigest:job.scopeDigest,
      actor:scope.actor,profile:scope.profile,hostRef:scope.hostRef,bootId:scope.bootId,
      sessionId:job.sessionId,inputSha256:job.inputSha256,reference};
    const ack=await publish({kind:'observation_reference',...identity});
    if(ack?.requestId!==reference || ack?.sessionId!==job.sessionId)throw Error('SDK binding ACK required');
    let result;
    try {
      // Read the actual current model for this invocation; no onboarding-model
      // cache, key export, history append, tool exposure or cwd/settings change.
      const model=await $.session.model();
      await publish({kind:'observation_dispatch',...identity});
      result=await $.model.complete({model,prompt:job.prompt,system:job.instructions,
        ...(job.timeoutMs===undefined?{}:{timeoutMs:job.timeoutMs})});
    } catch {
      await publish({kind:'observation_unknown',...identity});
      return {reference,state:'unknown'};
    }
    let state;
    if(result?.isAnswered===true && typeof result.text==='string')state='completed';
    else if(result?.isAnswered===false && ['api-error','empty-reply'].includes(result.reason))state='failed';
    else {
      // An aborted/unknown outcome is not upgraded merely because cancel or
      // timeout returned. Keep the original invocation without replay.
      await publish({kind:'observation_unknown',...identity});
      return {reference,state:'unknown'};
    }
    // publish owns private persistence of this actual closure completion and
    // collection-before-SDK-terminal. Model text is data, never terminal proof.
    await publish({kind:'observation_return',...identity,state,
      output:result.isAnswered?result.text:null,reason:result.isAnswered?'answered':result.reason});
    return {reference,state};
  }
}
