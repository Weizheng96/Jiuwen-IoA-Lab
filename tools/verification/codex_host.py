"""Explicit local Codex acceptance: bound SDK receipt, exact resume, real interrupt.

Run only when the operator authorizes real local Codex inference. Uses the existing
host authentication, never reads/copies auth files or mutates global configuration.
Codex is reached only through the explicit SDK Registry/Scheduler/S01 driver: P28
binds the actual session before the SDK tool, resume keeps the original binding and
cancellation goes through S05 on the original request. No startup probe gate.
"""
import argparse
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src/client/hosts/codex'),str(ROOT/'src/client/sdk')]
from ioalab_codex import CodexHost
from ioalab_sdk.runtime import AgentDefinition,ToolBinding,private_json
from tools.verification.s01_driver import ExplicitSubmission


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
    report={'status':'running','model':args.model,'proxyUsed':bool(args.proxy),'steps':[],'events':[]}
    calls=[];sessions=[];receipts=[];expected=['first','']
    def save():private_json(directory/'report.json',report)
    def step(name,result,seconds):
        report['steps'].append({'name':name,'result':asdict(result),'seconds':round(seconds,3)})
        report['calls']=list(calls)
        save();print(name+': '+result.state,flush=True)
    def deadline():
        return (datetime.now(timezone.utc)+timedelta(seconds=args.timeout)).isoformat()
    save()
    driver=None
    try:
        host=CodexHost(command,model=args.model);report['registration']=asdict(host.registration);save()
        driver=ExplicitSubmission(host,directory/'sdk-driver',actor='test-actor')
        ready=threading.Event()
        def observe(event):
            # Neutral S01 facts only; P28 has already persisted any session it reports.
            result=event.result
            report['events'].append({'kind':event.kind,'sessionId':result.session_id,'requestId':result.request_id,'state':result.state})
            if result.session_id and result.session_id not in sessions:sessions.append(result.session_id)
            if event.kind in ('session','accepted','running') and result.request_id:ready.set()
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
        def submit(text):
            return driver.start('test-interaction',definition,text,workspace=directory/'workspace',
                                on_tool=invoke,on_event=observe,deadline=deadline())
        start=time.monotonic()
        first=driver.wait(submit('Call local.receipt with value first and previousReceipt empty string.'),args.timeout+30)
        step('bound-tool',first,time.monotonic()-start)
        if first.state!='completed' or not first.terminal or len(calls)!=1:raise RuntimeError('First SDK tool receipt was not observed')
        expected[:]=['second',receipts[-1]]
        # Reopen the adapter and SDK driver over the original store; the binding, not a probe, selects the session.
        driver.close();driver=None
        host=CodexHost(command,model=args.model)
        driver=ExplicitSubmission(host,directory/'sdk-driver',actor='test-actor')
        start=time.monotonic()
        second=driver.wait(submit('Continue the same conversation: call local.receipt with value second and previousReceipt equal to the actual receipt from the preceding tool result.'),args.timeout+30)
        step('resume-original-session',second,time.monotonic()-start)
        if second.state!='completed' or not second.terminal or second.session_id!=first.session_id or len(calls)!=2:
            raise RuntimeError('Exact-session resume did not produce its bound SDK receipt')
        ready.clear()
        start=time.monotonic()
        cancelled=submit('Wait for the operator; this test turn will be interrupted. Do not call any tool.')
        if not ready.wait(args.timeout):raise RuntimeError('No bound native request before cancellation')
        report['cancel']=driver.cancel(cancelled);save()
        if report['cancel']['accepted'] is not True:raise RuntimeError('No matching active request accepted the interrupt')
        result=driver.wait(cancelled,args.timeout+30)
        step('cancel-original-turn',result,time.monotonic()-start)
        if result.state!='cancelled' or not result.terminal or result.session_id!=first.session_id or len(calls)!=2:
            raise RuntimeError('Cancellation lacks its real terminal or produced an unexpected tool call')
        report.update(status='passed',calls=calls,sessionId=first.session_id,
            scope='Local host only through SDK Registry/Scheduler/S01; no platform connection, contracts, payments, native production tools or desktop-chat notification acceptance')
        save();return 0
    except Exception as exc:
        report.update(status='failed',errorType=type(exc).__name__)
        save();print('Acceptance failed: '+type(exc).__name__+'; inspect the original report and runs',flush=True);return 1
    finally:
        if driver:driver.close()


if __name__=='__main__':raise SystemExit(main())
