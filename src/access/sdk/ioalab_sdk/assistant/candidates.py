"""Original candidate validation and read-only comparison on existing role jobs."""
import copy
import io
from pathlib import PurePosixPath
import threading
import zipfile

import httpx
from a2c.card import card_digest,request_digest,validate_card,validate_request,verify_request
from a2c.codec import digest,utcnow,parse_time,parse_duration,fmt_time
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import header_of,check_window
from a2c.slots import verify_template,verify_card
from a2c.template import acceptance_of,accept_within,verifier_refs
from a2c.verifier.package import load,MANIFEST

from ..adapters import ProtocolStore
from ..adapters.market import reputation_evidence
from ..runtime.protocol import RuntimeFault
from ..runtime.scheduling import JobOutcome


def source_record(trading,record):
    if record.get('template',{}).get('formation')!='proposal':return {}
    card=record.get('result',{}).get('card')
    if not card or not record.get('interactionId'):return {}
    store=ProtocolStore(trading.store,trading.identity.agent_id,record['interactionId'])
    original=store.get('cards',card_digest(card))
    return {'request':copy.deepcopy(original['request'])} if original and original.get('request') else {}


def checked_candidate(trading,record,*,task=None,keys=None,source=None,require_current=True):
    """One owner for comparison and first contract confirmation, using A2C rules."""
    if record.get('status')!='completed' or 'card' not in record.get('result',{}):
        raise RuntimeFault('not_ready','Candidate has no completed original quote')
    try:
        template=record['template'];card=record['result']['card'];result=record['result']
        context=(task or trading.role.delegation.task(record['taskRef']))['context']
        expected=trading.protocol_id(record['service'])
        if (result.get('taskRef')!=record['taskRef'] or result.get('service')!=record['service']
                or template['provider']!=expected or card['provider']!=expected
                or result.get('formation')!=template['formation']
                or card['client']!={'id':trading.identity.signer.id,'jwk':trading.identity.signer.inline_jwk}
                or card.get('user')!={'id':trading.identity.user.id,'jwk':trading.identity.user.inline_jwk}
                or card['delegationScopeDigest']!=digest(context)):
            raise RuntimeFault('invalid_input','Candidate identity or delegation scope differs from the original task')
        keys=keys or Directory(fetch=trading.public.jwks);now=utcnow()
        for header in verify_template(template,keys).values():check_window(header,now)
        request=None;request_ts=None
        if template['formation']=='proposal':
            original=source if source is not None else source_record(trading,record)
            request=original.get('request')
            if not request:raise RuntimeFault('not_ready','Restore the original signed candidate request')
            validate_request(template,request);header=verify_request(request);check_window(header,now)
            if request_digest(request)!=request_digest(record['subject']):
                raise RuntimeFault('invalid_input','Candidate differs from its original request')
            request_ts=parse_time(header['a2c-ts'])
        elif template['formation']!='countersign' or card!=record['subject']:
            raise RuntimeFault('invalid_input','Use the original unsigned countersign candidate')
        validate_card(template,card,request=request)
        chain=verify_card(template,card,keys,request_ts=request_ts)
        until=parse_time(template['validUntil'])
        if template['formation']=='proposal':
            if set(chain.ts)!={2,3}:raise RuntimeFault('invalid_input','Candidate must have original slots 2 and 3 without adoption')
            for slot in (2,3):check_window(header_of(card['signatures'][str(slot)]),now)
            until=min(until,chain.ts[3]+parse_duration(template['deadlines']['respondWithin']))
        elif any(card['signatures'].values()):raise RuntimeFault('invalid_input','A countersign candidate must still be unsigned')
        current=until>utcnow()
        if require_current and not current:raise RuntimeFault('expired','This candidate can no longer form a new contract')
        return {'template':template,'card':card,'until':until,'current':current}
    except (KeyError,TypeError,ValueError,IndexError):
        raise RuntimeFault('invalid_input','Stored candidate evidence is incomplete or inconsistent') from None


class PackageReview:
    def __init__(self,fetch):self.fetch=fetch;self.remaining=128*1024;self.blobs={};self.packages={}

    def read(self,kind,ref):
        key=digest({'digest':ref['digest'],'kind':kind,'id':ref['id']})
        if key in self.packages:
            return {'reviewRef':key,'reference':copy.deepcopy(ref),'kind':kind,'complete':self.packages[key]['complete']}
        if ref['digest'] not in self.blobs:self.blobs[ref['digest']]=self.fetch(ref['uri'])
        blob=self.blobs[ref['digest']];package=load(blob,ref,kind)
        budget=min(32*1024,self.remaining);files={};omitted=[]
        # Read the hash-verified blob, not possibly modified extracted cache files.
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for item in sorted(archive.infolist(),key=lambda item:item.filename):
                if item.is_dir() or item.filename==MANIFEST:continue
                reason=None
                if PurePosixPath(item.filename).suffix not in ('.py','.md','.json'):reason='non_text'
                elif item.file_size>budget:reason='size_limit'
                else:
                    raw=archive.read(item)
                    try:text=raw.decode('utf-8-sig')
                    except UnicodeDecodeError:reason='non_utf8'
                    else:files[item.filename]=text;budget-=len(raw);self.remaining-=len(raw)
                if reason:omitted.append({'name':item.filename,'bytes':item.file_size,'reason':reason})
        value={'reviewRef':key,'reference':copy.deepcopy(ref),'kind':kind,'manifest':copy.deepcopy(package.manifest),
               'files':files,'omitted':omitted,'complete':not omitted}
        self.packages[key]=value
        return {'reviewRef':key,'reference':copy.deepcopy(ref),'kind':kind,'complete':value['complete']}


class Comparisons:
    def __init__(self,trading):
        self.trading,self.role,self.runtime=trading,trading.role,trading.runtime
        self.actor=trading.identity.agent_id;self.lock=threading.RLock()

    def start(self,operation,args):
        references=args['quoteRefs']
        if (not isinstance(references,list) or not 1<=len(references)<=32
                or any(not isinstance(item,str) or not 1<=len(item)<=128 for item in references)
                or len(set(references))!=len(references)):
            raise RuntimeFault('invalid_input','Use one to 32 distinct original quoteRefs')
        task=self.role.delegation.task(args['taskRef']);scheduler=self.runtime.scheduler
        reference=scheduler.reference(self.actor,'assistant.compare',operation)
        with self.lock:
            previous=scheduler.records(self.actor,('assistant.compare',),reference=reference)
            if previous:
                if previous[0]['payload']['arguments']!=args:raise RuntimeFault('id_conflict','Original comparison arguments changed')
                job=scheduler.get(reference)
            else:
                quotes=[]
                with self.trading.store.lock:
                    for ref in references:
                        record=self.trading.store.checkpoint(self.trading.namespace,'quote:'+ref)
                        if not record or record.get('taskRef')!=args['taskRef']:
                            raise RuntimeFault('forbidden','Every quote must belong to this local task')
                        try:original=source_record(self.trading,record)
                        except (RuntimeFault,A2CError,KeyError,TypeError,ValueError):original={}
                        observed=self.trading.status(ref)['status']
                        # Quote materials remain in their protocol owner; the read job
                        # only needs signed terms and the original request metadata.
                        snapshot={name:record[name] for name in ('quoteRef','taskRef','service','status','phase',
                            'template','subject','result','interactionId') if name in record}
                        quotes.append({'record':snapshot,'source':original,'observedStatus':observed})
                job=scheduler.submit(self.actor,args['taskRef'],'assistant.compare',
                    {'arguments':copy.deepcopy(args),'task':copy.deepcopy(task),'quotes':quotes},operation_id=operation)
        state=job['status'] if job['status'] in ('completed','rejected','failed','unknown') else 'accepted'
        return {'status':state,'data':{'taskRef':args['taskRef'],'reference':{'kind':'job','id':reference},'job':job}}

    def run(self,job):
        payload=job['payload'];keys=Directory(fetch=self.trading.public.jwks)
        review=PackageReview(self.runtime.account.fetch_package);rows=[];reputations={}
        for item in payload['quotes']:
            record=item['record'];row={key:record[key] for key in ('quoteRef','service')}
            try:
                facts=checked_candidate(self.trading,record,task=payload['task'],keys=keys,source=item['source'],require_current=False)
                template,card=facts['template'],facts['card']
                row.update(formation=template['formation'],state=('offered' if template['formation']=='proposal' else 'unsigned') if facts['current'] else 'expired',
                    validUntil=fmt_time(facts['until']),cardDigest=card_digest(card),templateDigest=card['templateDigest'],
                    derived=copy.deepcopy(card['derived']),proposalFee=template['pricing'].get('proposalFee',0),
                    deliverables=[{**copy.deepcopy(value),'spec':copy.deepcopy(card['spec'][value['name']]),
                        'acceptance':copy.deepcopy(acceptance_of(template,card)[value['name']]),
                        'acceptWithin':accept_within(template,card,value['name'])} for value in template['deliverables']],
                    deadlines=copy.deepcopy(template['deadlines']),signers=copy.deepcopy(card['signers']),
                    authorizableSteps=copy.deepcopy(template.get('authorizableSteps',[])),paymentProvider=copy.deepcopy(card['paymentProvider']),
                    inputs={name:{**copy.deepcopy(value),'sendAt':'before-signing' if any(
                        material['name']==name and material.get('reviewBeforeSigning') for material in template.get('inputs',[])) else 'work.start'} for name,value in card['inputs'].items()})
                provider=card['provider']
                if provider not in reputations:reputations[provider]=reputation_evidence(self.runtime.account,self.trading.public,provider,keys=keys)
                row['reputation']=reputations[provider];evidence=[]
                for target,name,kind,ref in verifier_refs(template,card):
                    try:package=review.read(kind,ref)
                    except A2CError as exc:
                        package={'reference':copy.deepcopy(ref),'kind':kind,'complete':False,'status':'invalid','error':{'code':exc.code}}
                    except (RuntimeFault,httpx.HTTPError):
                        package={'reference':copy.deepcopy(ref),'kind':kind,'complete':False,'status':'unavailable','error':{'code':'read_unavailable'}}
                    except (ValueError,KeyError,TypeError):
                        package={'reference':copy.deepcopy(ref),'kind':kind,'complete':False,'status':'invalid','error':{'code':'invalid_evidence'}}
                    evidence.append({'target':target,'name':name,**package})
                row['verifierEvidence']=evidence
            except (A2CError,RuntimeFault,KeyError,TypeError,ValueError) as exc:
                state=record.get('status') or item.get('observedStatus')
                row.update(state=state if state in ('rejected','failed','unknown') else 'pending' if state!='completed' else 'invalid',
                           error={'code':getattr(exc,'code','invalid_evidence')})
            rows.append(row)
        evaluated=utcnow()
        for row in rows:
            if row.get('state') in ('offered','unsigned') and parse_time(row['validUntil'])<=evaluated:row['state']='expired'
        context=payload['task']['context']
        return JobOutcome('completed',{'taskRef':payload['arguments']['taskRef'],'evaluatedAt':fmt_time(evaluated),
            'purpose':context['purpose'],'budget':context['budget'],'proposalFeeLimit':context['proposalFeeLimit'],
            'quoteCount':len(rows),'serviceCount':len({row['service'] for row in rows}),'candidates':rows,
            'verifierPackages':list(review.packages.values())})
