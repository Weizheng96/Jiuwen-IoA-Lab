// Single public caller transport. Native metadata is never taken from tool arguments.
export async function post($,url,token,bootId,body) {
  const parsed=new URL(url);
  if(parsed.protocol!=='http:' || parsed.hostname!=='127.0.0.1' || parsed.username || parsed.password)
    throw Error('native_entry_endpoint_invalid');
  const response=await $.http.fetch(url,{method:'POST',headers:{'Content-Type':'application/json',
    'Authorization':'Bearer '+token,'X-Ioa-Instance':bootId},body:JSON.stringify(body)});
  const value=JSON.parse(response.text);
  if(!response.ok && !['rejected','unknown'].includes(value?.status))throw Error('native_entry_request_failed');
  return value;
}

export async function loadBindings($) {
  // This file is hydrated by P07/P08 into the installed extension copy. It holds
  // paths only. Private runtime descriptors/grants never ship in the wheel/Skill.
  const value=JSON.parse(await $.fs.read($.plugin.root+'/entry-binding.json'));
  if(Object.keys(value).sort().join(',')!=='format,hostRef,profiles,setupOperation,sourceOperations' ||
      value.format!=='ioalab.native-entry/1' || typeof value.hostRef!=='string' || !value.hostRef ||
      !value.profiles || typeof value.profiles!=='object' || Array.isArray(value.profiles) ||
      typeof value.setupOperation!=='string' || !value.setupOperation ||
      !Array.isArray(value.sourceOperations) || value.sourceOperations.some(op=>typeof op!=='string' || !op) ||
      new Set(value.sourceOperations).size!==value.sourceOperations.length || value.sourceOperations.includes(value.setupOperation) ||
      Object.entries(value.profiles).some(([role,binding])=>!['assistant','provider'].includes(role) || !binding ||
        Object.keys(binding).sort().join(',')!=='callerCommand,descriptorFile,ensureCommand,profile' ||
        typeof binding.profile!=='string' || !binding.profile || typeof binding.descriptorFile!=='string' || !binding.descriptorFile ||
        ['callerCommand','ensureCommand'].some(key=>!Array.isArray(binding[key]) || !binding[key].length ||
          binding[key].some(arg=>typeof arg!=='string' || !arg))))
    throw Error('native_entry_binding_invalid');
  return {...value,profiles:Object.fromEntries(Object.entries(value.profiles).map(([role,binding])=>
    [role,{...binding,hostRef:value.hostRef,sourceOperations:value.sourceOperations,setupOperation:value.setupOperation}]))};
}

export async function descriptor($,binding) {
  const value=JSON.parse(await $.fs.read(binding.descriptorFile));
  if(value.adapterId!=='claude_code' || value.hostRef!==binding.hostRef || !value.grant || !value.bootId || !value.controlUrl ||
      typeof binding.profile!=='string' || value.profile!==binding.profile)
    throw Error('native_entry_binding_incomplete');
  return value;
}

async function call($,binding,input,contextRef) {
  try {
    const response=await $.process.run(binding.callerCommand,{stdin:JSON.stringify({...input,contextRef})});
    if(response.isStdoutTruncated)throw Error('native_public_receipt_truncated');
    const value=JSON.parse(response.stdout);
    if(value.operationId!==input.operationId || !['completed','rejected','waiting','unknown','failed'].includes(value.status))
      throw Error('native_public_receipt_unverified');
    return value;
  } catch {
    // A lost common-caller receipt never proves non-execution. Do not rerun it.
    const error=Error('native_public_call_unknown');error.submissionUnknown=true;throw error;
  }
}

export async function invoke($,binding,input,onReady) {
  if(!input || Object.keys(input).some(key=>!['operation','operationId','arguments'].includes(key)) ||
      typeof input.operation!=='string' || typeof input.operationId!=='string' || !input.operationId ||
      input.operationId.length>128 || !input.arguments || typeof input.arguments!=='object' || Array.isArray(input.arguments))
    throw Error('native_entry_arguments_invalid');
  if(!Array.isArray(binding.sourceOperations) || !Array.isArray(binding.callerCommand))
    throw Error('native_entry_caller_binding_missing');
  // The shared public catalog supplies this declaration. Controls/diagnostics
  // do not wake the client, inspect a grant or capture a native source.
  const setup=input.operation===binding.setupOperation;
  if(setup && Object.keys(input.arguments).length)throw Error('native_setup_arguments_invalid');
  if(!setup && !binding.sourceOperations.includes(input.operation))return call($,binding,input,null);
  if(!Array.isArray(binding.ensureCommand))throw Error('native_entry_ensure_binding_missing');
  // Only this explicit foreground entry can wake the existing client. The
  // background poller never executes an ensure command or starts a new model.
  const ensured=await $.process.run(binding.ensureCommand);
  let readiness;
  try {readiness=JSON.parse(ensured.stdout);}catch{throw Error('native_client_ensure_unverified');}
  if(ensured.exitCode!==0 || ensured.isStdoutTruncated || readiness.status!=='completed') {
    return {operationId:input.operationId,status:readiness.status==='waiting'?'waiting':'rejected',
      error:{code:'not_ready',phase:'ensure_client',dispatch:'not_submitted',
        action:readiness.error?.recovery??'Inspect the original client startup; no business call was submitted'}};
  }
  const grant=await descriptor($,binding);
  if(onReady)await onReady(grant);
  const captured={sessionId:await $.session.id(),workspace:await $.session.cwd(),captureId:crypto.randomUUID()};
  const source=await post($,grant.url,grant.grant,grant.bootId,captured);
  if(!source.data?.entryRef)throw Error('native_source_registration_failed');
  const contextRef={entryRef:source.data.entryRef};
  const result=await call($,binding,input,contextRef);
  if(!setup || result.status!=='waiting')return result;
  // Only the explicit public setup operation creates a SDK intent. First
  // recover an already saved receipt; a different/new chat cannot reselect it.
  const identity={sessionId:captured.sessionId,generation:binding.observerGeneration};
  if(typeof identity.generation!=='string' || !identity.generation)throw Error('native_setup_generation_missing');
  const control=async body=>{
    try {
      const response=await post($,grant.controlUrl,grant.grant,grant.bootId,{...body,...identity});
      if(response.status!=='completed')throw Error('native_setup_control_failed');
      return response.data;
    } catch {const error=Error('native_setup_receipt_unknown');error.submissionUnknown=true;throw error;}
  };
  const restored=await control({kind:'entry_recover'});
  if(restored===null) {
    // A reserved target without its saved receipt cannot authorize recapture.
    if(result.data?.phase!=='intent')return result;
    const response=await $.process.run([binding.callerCommand[0],'-I','-X','utf8','-c',
      'from ioalab_claude_code.native_process import capture_main; capture_main()'],{timeoutMs:10000});
    if(response.exitCode!==0 || response.isStdoutTruncated)throw Error('native_setup_capture_unavailable');
    let capture;try{capture=JSON.parse(response.stdout);}catch{throw Error('native_setup_capture_unverified');}
    // Actual loaded extension location, never the client's guessed directory.
    capture.extensionDirectory=$.plugin.root;
    await control({kind:'entry_setup',entryRef:contextRef.entryRef,capture});
  }
  // The original SDK ledger is the status authority. Same op, no new intent,
  // no business call and no native/model prompt is submitted by setup.
  return call($,binding,input,contextRef);
}
