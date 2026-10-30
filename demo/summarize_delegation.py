"""Attach an allowlisted summary of actual provider and local-verifier evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from artifacts import read_json, require, validate_real_case, write_json


def attach_business_answer(cfg, recording, proof):
    from artifacts import BUSINESS_SETTINGS_REPLY, validate_business_operator, media_digest, native_user_text
    actions=[a for a in proof.get('operatorActions',[]) if a.get('kind')=='message']
    if not actions:return
    require(len(actions)==1 and actions[0]['id']=='business-settings-answer','No technical rescue prompts')
    root=Path(__file__).resolve().parents[1]
    sessions=[p.parent for p in (root/'.swarm-runtime/agent/sessions').glob('*/metadata.json') if read_json(p).get('user_id')==cfg['swarmUserId']]
    require(len(sessions)==1,'Expected same native session')
    trace=sessions[0]/'history.jsonl';events=[json.loads(line) for line in trace.read_text('utf-8').splitlines()]
    indices=[i for i,e in enumerate(events) if native_user_text(e)==BUSINESS_SETTINGS_REPLY]
    require(len(indices)==1,'Actual native business reply is missing or duplicated')
    questions=[q['question'] for e in events[:indices[0]] if e.get('event_type')=='chat.ask_user_question' for q in e.get('questions',[]) if '规格参数' in q.get('question','')] or [e['content'] for e in events[:indices[0]] if e.get('event_type')=='chat.final' and '规格参数' in e.get('content','')]
    require(questions,'Actual native specification request is missing')
    proof['businessSettingsAnswer']={'text':BUSINESS_SETTINGS_REPLY,'question':questions[-1],'source':'actual native Jiuwen user message in original recording browser','nativeTraceSha256':media_digest(trace)}
    if not any(t['kind']=='settings' for t in proof['userTurns']):proof['userTurns'].insert(2,{'kind':'settings','text':BUSINESS_SETTINGS_REPLY})
    actual=[text for e in events if (text:=native_user_text(e)) is not None];actual[0]=actual[0].removeprefix('ioalab-delegate ')
    require(actual==[t['text'] for t in proof['userTurns']],'Recorded turns differ from native user history')
    validate_business_operator(proof)
    segments=read_json(recording/'segments.json')
    if not any(s['id']=='settings' for s in segments):segments.insert(7,{'id':'settings','start':max(0,round(actions[0]['at']-2,3)),'duration':18,'source':'screen.webm'})
    write_json(recording/'segments.json',segments)


def summarize(config, recording):
    cfg=read_json(config);folder=Path(cfg['folder']).resolve()
    proof=read_json(recording/'evidence.json');require(proof.get('case')=='delegation','Expected delegation evidence')
    require(proof.get('flow')=='nail-original','Expected original nail-booking flow')
    digest=proof['cardDigest'];suffix=digest.removeprefix('sha256:')
    require(len(suffix)==64 and all(c in '0123456789abcdef' for c in suffix),'Invalid card digest')
    matches=list((folder/'providers').glob('*/jobs/deliver-'+suffix))
    require(len(matches)==1,'Expected one provider job for this contract')
    job=matches[0];result=read_json(job/'swarm-result.json')
    require(result['status']=='completed','Provider process did not complete')
    counts=Counter();statuses=[];delivered=False
    for line in (job/'swarm-events.jsonl').read_text(encoding='utf-8').splitlines():
        record=json.loads(line)
        if record.get('event_type')!='chat.tool_result':continue
        item=record['payload'];name=item.get('tool_name')
        if name not in ('ioa_inspect','ioa_selftest','ioa_deliver'):continue
        counts[name]+=1
        if name=='ioa_inspect':continue
        rendered=item.get('rendered_result')
        if not isinstance(rendered,str):continue
        try:data=json.loads(rendered)
        except json.JSONDecodeError:continue
        if name=='ioa_selftest':statuses.append(data.get('status'))
        elif data.get('status')=='delivered':delivered=True
    require(statuses and statuses[-1]=='pass' and delivered,'Actual selftest and SDK delivery are missing')
    path=folder/'client-b/contracts.sqlite'
    with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as db:
        if proof.get('executionPath')=='generic-primitives':
            require(db.execute("SELECT count(*) FROM documents WHERE tbl='delegations'").fetchone()[0]==0,'Generic skill recording must not use a scenario workflow')
            row=db.execute("SELECT v FROM documents WHERE tbl='verification_reports' AND k=?",(digest+':1',)).fetchone()
            require(row is not None,'Actual primitive verifier report is missing')
            report=json.loads(row[0])
        else:
            tasks=[json.loads(v) for v, in db.execute("SELECT v FROM documents WHERE tbl='delegations'")]
            task=next(t for t in tasks if t.get('cardDigest')==digest)
            require(task['gate']=='completed','Legacy task must be completed');report=task['report']
    checks=[c for group in report['checks'] for c in group]
    require(report['status']=='pass' and len(checks)==10 and all(c.get('ok') for c in checks),'Actual local ten-check report is missing')
    proof['production']={'source':'actual provider job events','processStatus':result['status'],
        'toolCounts':dict(counts),'selftestStatuses':statuses,'submission':'delivered',
        'processRuns':len(list(job.glob('swarm-result-*.json')))}
    proof['verification']={'source':'personal assistant local WASM','status':'pass','checkCount':len(checks),'allPassed':True}
    attach_business_answer(cfg,recording,proof)
    validate_real_case(proof)
    write_json(recording/'evidence.json',proof)
    print(json.dumps({'production':proof['production'],'verification':proof['verification']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--recording',type=Path,required=True)
    args=p.parse_args();summarize(args.config,args.recording.resolve())
