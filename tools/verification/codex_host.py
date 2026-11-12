"""Explicit local Codex acceptance: bound SDK receipt, exact resume, real interrupt.

Run only when the operator authorizes real local Codex inference. Uses the existing
host authentication, never reads/copies auth files or mutates global configuration.
"""
import argparse
from dataclasses import asdict,replace
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src/hosts/codex'),str(ROOT/'src/access/sdk')]
from ioalab_codex import CodexHost
from ioalab_sdk.runtime import AgentDefinition,HostTurn,ToolBinding,private_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex',required=True,type=Path)
    parser.add_argument('--model',required=True)
    parser.add_argument('--report-dir',required=True,type=Path)
    parser.add_argument('--proxy',help='Optional explicitly chosen process-only HTTP proxy')
    parser.add_argument('--timeout',type=float,default=120)
    args=parser.parse_args(argv)
    if not args.codex.is_absolute() or not args.codex.is_file():parser.error('--codex must name the installed executable by absolute path')
    if args.proxy:
        parsed=urlsplit(args.proxy)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            parser.error('Use an HTTP(S) proxy without credentials or query parameters')
        os.environ['HTTPS_PROXY']=args.proxy;os.environ['HTTP_PROXY']=args.proxy
    directory=args.report_dir.resolve();directory.mkdir(parents=True,exist_ok=False)
    command=[str(args.codex)]
    report={'status':'running','model':args.model,'proxyUsed':bool(args.proxy),'steps':[]}
    def save():private_json(directory/'report.json',report)
    def step(name,result,seconds):
        report['steps'].append({'name':name,'result':asdict(result),'seconds':round(seconds,3)})
        report['calls']=list(calls)
        save();print(name+': '+result.status,flush=True)
    save()
    try:
        host=CodexHost(command,model=args.model);report['capabilities']=host.probe();save()
        if not report['capabilities']['available']:raise RuntimeError('Local Codex probe failed')
        calls=[];sessions=[];receipts=[];expected=['first','']
        report['events']=[]
        def observe(record):
            if record['method']=='item/completed' and record['payload'].get('item',{}).get('type')=='agentMessage':
                report['events'].append({'sessionId':record['session_id'],'message':record['payload']['item'].get('text')})
        def invoke(name,arguments,call_id):
            if name!='local.receipt' or not sessions or arguments!={'value':expected[0],'previousReceipt':expected[1]}:
                raise RuntimeError('The actual tool call lost its bound session or previous receipt')
            receipt=uuid.uuid4().hex
            calls.append({'name':name,'arguments':arguments,'callId':call_id,'sessionId':sessions[-1]})
            receipts.append(receipt)
            return {'receipt':receipt,'value':arguments['value']}
        definition=AgentDefinition('local-codex-acceptance',
            'Use only local.receipt. Call it exactly once as requested, then report its actual receipt. '
            'A previousReceipt must come from the preceding actual tool result in this same conversation. '
            'Never invent a tool result or use other tools.',(
                ToolBinding('local.receipt','Return a harmless local test receipt',{'type':'object','properties':{
                    'value':{'type':'string'},'previousReceipt':{'type':'string'}},
                    'required':['value','previousReceipt'],'additionalProperties':False},lambda *_:None),))
        turn=HostTurn('acceptance-first','test-actor','test-interaction',definition,
            'Call local.receipt with value first and previousReceipt empty string.',str(directory/'workspace'),
            args.timeout,on_session=sessions.append,on_tool=invoke,on_event=observe)
        start=time.monotonic();first=host.run(turn);step('bound-tool',first,time.monotonic()-start)
        if first.status!='completed' or len(calls)!=1:raise RuntimeError('First SDK tool receipt was not observed')
        expected[:]=['second',receipts[-1]]
        host=CodexHost(command,model=args.model)
        if not host.probe()['available']:raise RuntimeError('Reopened Codex probe failed')
        resumed=replace(turn,operation_id='acceptance-resume',session_id=first.session_id,
            input='Continue the same conversation: call local.receipt with value second and previousReceipt equal to the actual receipt from the preceding tool result.')
        start=time.monotonic();second=host.run(resumed);step('resume-original-session',second,time.monotonic()-start)
        if second.status!='completed' or second.session_id!=first.session_id or len(calls)!=2:
            raise RuntimeError('Exact-session resume did not produce its bound SDK receipt')
        ready=threading.Event();results=[];failures=[]
        def event(record):
            if record['method']=='turn/started':ready.set()
        cancel_turn=replace(turn,operation_id='acceptance-cancel',session_id=first.session_id,
            input='Wait for the operator; this test turn will be interrupted. Do not call any tool.',on_event=event)
        def run_cancel():
            try:results.append(host.run(cancel_turn))
            except Exception as exc:failures.append(type(exc).__name__)
        worker=threading.Thread(target=run_cancel)
        start=time.monotonic();worker.start()
        try:
            if not ready.wait(args.timeout):raise RuntimeError('No real turn-start event before cancellation')
            if not host.cancel(cancel_turn.operation_id):raise RuntimeError('No matching active operation')
        finally:worker.join(args.timeout+10)
        if worker.is_alive() or failures or not results:raise RuntimeError('Cancelled turn did not return its original result')
        step('cancel-original-turn',results[0],time.monotonic()-start)
        if results[0].status!='cancelled' or results[0].session_id!=first.session_id or len(calls)!=2:
            raise RuntimeError('Cancellation lacks its real terminal or produced an unexpected tool call')
        report.update(status='passed',calls=calls,sessionId=first.session_id,
            scope='Local host only; no platform connection, contracts, payments, native production tools or desktop-chat notification acceptance')
        save();return 0
    except Exception as exc:
        report.update(status='failed',errorType=type(exc).__name__)
        save();print('Acceptance failed: '+type(exc).__name__+'; inspect the original report and runs',flush=True);return 1


if __name__=='__main__':raise SystemExit(main())
