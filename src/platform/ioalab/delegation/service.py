"""委托公共协议服务：A2C 市场、认证文书、信誉及裁定，不承担注册发现。

市场不看钱款收付，只验签名链。仲裁到裁定文书为止，不划转。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from a2c import objects
from a2c.card import card_digest
from a2c.codec import digest_bytes, fmt_time
from a2c.common import Clock, Directory
from a2c.jws import Signer

from ..storage import DB, jdump


class MarketService:
    def __init__(self, signer: Signer, *, keys: Directory, fetch: Callable[[str], bytes], store, db: DB,
                 clock: Clock | None = None, models=None) -> None:
        self.signer = signer
        self.keys = keys
        self.db = db
        # Unconfigured dispute models are rejected by the A2C snapshot validator.
        from .market import AdmissionMarket
        self.market = AdmissionMarket(signer, keys=keys, fetch=fetch, clock=clock, store=store, recognized_services={}, models=models or {})
        self.now = self.market.now
        self.request_outcomes = lambda provider: ()

    @property
    def id(self) -> str:
        return self.signer.id

    def template(self, reference):
        """Read the original A2C template through its owning component."""
        return self.market._template(reference)

    def card(self, reference):
        """Read the original anchored A2C card, without exposing store namespaces."""
        return self.market.store.get('cards',reference)

    def counts(self):
        """Public protocol-artifact counts for observation only."""
        return {'endorsed':len(self.market.store.all('templates')),
                'snapshots':len(self.market.store.all('snapshots'))}


    def endorse_service_templates(self, provider, key, templates, payment_provider):
        """Original A2C endorsement; reuse a completed snapshot after a lost response."""
        from a2c.template import validate
        from a2c.slots import verify_template
        from a2c.common import Directory
        from a2c.codec import parse_time
        from ..errors import LabError
        keys=Directory()
        keys.add(provider,{'keys':[key]})
        checked=[]
        for template in templates:
            validate(template,endorsed=False)
            if template['provider']!=provider or template['formation'] not in ('proposal','countersign'):
                raise LabError('invalid_input','Ordinary services use their bound identity and proposal or countersign templates')
            if set(template.get('signatures',{}))!={'provider'}:
                raise LabError('invalid_input','Upload the original provider-signed template without another endorsement')
            if template['pricing'].get('paymentProviders')!=[payment_provider]:
                raise LabError('invalid_input','Template must select this platform payment provider')
            verify_template(template,keys,endorsed=False)
            if parse_time(template['validUntil'])<=self.now():
                raise LabError('expired','Template is no longer valid for new publication',status=409)
            checked.append(template)
        self.keys.add(provider,{'keys':[key]})
        endorsed=[]
        for template in checked:
            snapshot=self.market.store.get('snapshots',template['templateDigest'])
            original=snapshot.get('template') if snapshot else None
            unsigned_market=lambda t:{**t,'signatures':{k:v for k,v in t['signatures'].items() if k!='market'}}
            if original and original.get('signatures',{}).get('market') and unsigned_market(original)==template:
                verify_template(original,self.keys)
                endorsed.append(original)
            else:
                endorsed.append(self.market.op_template_endorse({'template':template},{},None).body['template'])
        return endorsed

    # ── 服务认证（协议外）────────────────────────────────────────────
    def prepare_proposal(self,template,card,party):
        """Original market validation/signature in an isolated, unpublished Store."""
        from a2c.market import Market
        from a2c.common import Store
        keys=Directory();keys.add(party['id'],{'keys':[party['jwk']]})
        keys.add(self.id,{'keys':[self.market.signer.jwk]})
        staged=Market(self.market.signer,keys=keys,fetch=self.market.fetch,clock=self.market.now,store=Store(),
                      recognized_services=self.market.recognized_services,models=self.market.models)
        staged.store.put('snapshots',template['templateDigest'],{'template':template})
        reply=staged.op_card_endorse({'card':card},{},None)
        return reply.body,staged.store.get('snapshots',card_digest(card))

    def commit_snapshot_in(self,conn,key,snapshot):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Market snapshot requires the owned application transaction')
        self.market.store.put('snapshots',key,snapshot)

    def prepare_contract(self,template,op,card):
        """Use the original Market in an unpublished store; no I/O in commit."""
        from a2c.market import Market
        from a2c.common import Store
        staged=Market(self.signer,keys=self.keys,fetch=self.market.fetch,clock=self.now,store=Store(),
                      recognized_services=self.market.recognized_services,models=self.market.models)
        staged.store.put('snapshots',template['templateDigest'],{'template':template})
        reply=getattr(staged,'op_'+op.replace('.','_'))({'card':card},{},None)
        return reply.body,{table:staged.store.all(table) for table in ('cards','ledger')}

    def commit_contract_in(self,conn,changes):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Market facts require the owned application transaction')
        for table,values in changes.items():
            if table not in ('cards','ledger'):raise RuntimeError('Unexpected market store namespace')
            for key,value in values.items():self.market.store.put(table,key,value)


    def operation_in(self, conn, actor, operation):
        """Saved application `a2c.market` receipt: owner, fingerprint and original result, or None."""
        if conn is not self.db.conn:raise RuntimeError('Use the owned market operation connection')
        row=conn.execute('SELECT owner,fingerprint,result FROM delegation_market_operations WHERE actor=? AND id=?',(actor,operation)).fetchone()
        return None if row is None else {'owner':row['owner'],'fingerprint':row['fingerprint'],'result':json.loads(row['result'])}

    def record_operation_in(self, conn, owner, actor, operation, fingerprint, result):
        """Save the receipt with the market facts committed in the same transaction."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Market operation receipt requires the owned application transaction')
        conn.execute('INSERT INTO delegation_market_operations VALUES(?,?,?,?,?)',(owner,actor,operation,fingerprint,json.dumps(result)))

    # ── 信誉条目（协议外；依据是结清的卡与裁定）───────────────────────
    def reputation(self, provider_id: str) -> dict:
        ledger = self.market.store.all("ledger")
        rulings = self.market.store.all("rulings")
        total = success = 0
        for cd, e in ledger.items():
            pid = e.get("provider") or (e.get("card") or {}).get("provider")
            if pid != provider_id:
                continue
            outcome = e.get("outcome")
            if outcome == "completed":
                total += 1
                success += 1
            elif outcome == "arbitrated":
                r = rulings.get(cd, {})
                if r.get("liable") not in ("client", "provider"):
                    continue
                total += 1
                if r.get("liable") == "client":
                    success += 1
            # client-terminated：不计
        for ruling in self.request_outcomes(provider_id):
            if ruling.get("liable") in ("client", "provider"):
                total += 1
                success += ruling["liable"] == "client"
        entry = {"kind": "reputation", "provider": provider_id, "completed": total,
                 "successRate": round(success / total, 3) if total else None, "issuedAt": fmt_time(self.now())}
        entry["signature"] = _sign_free(self.signer, entry, self.now())
        return entry

    # ── 仲裁（协议外接口）────────────────────────────────────────────


    def evaluate_case(self,basis,route,materials):
        """Use original signed snapshots and A2C criteria, outside a DB transaction."""
        from dataclasses import asdict
        from ..errors import LabError
        if route=='deterministic':
            liable={'start-overdue':'provider','deliver-overdue':'provider','proposal-overdue':'provider',
                    'accept-overdue':'client','payment-overdue':'client','authorize-overdue':'client'}.get(basis['ground'])
            if liable is None:raise LabError('history_gap','This ground requires independent review',status=409)
            return {'rulingKind':'deadline','liable':liable,'evidence':{'ground':basis['ground'],'status':'deadline_passed'}}
        if route!='recompute' or basis['ground']!='acceptance-failed':
            raise LabError('history_gap','This ground requires independent review',status=409)
        files=materials.get('deliveryFiles',{})
        manifest=basis['card']['fulfillment']['periods'][basis['period']-1]['manifest']
        if {name:digest_bytes(data) for name,data in files.items()}!={item['name']:item['digest'] for item in manifest['files']}:
            raise LabError('history_gap','Submitted bytes differ from the original signed manifest',status=409)
        result=self.market.recompute(basis['card'],basis['period'],files)
        if result.status not in ('pass','fail'):
            raise LabError('unknown_result','Original recompute did not return a final program result',status=409)
        return {'rulingKind':'recompute','liable':'client' if result.status=='pass' else 'provider',
            'evidence':{'status':result.status,'runs':[asdict(run) for run in result.runs],'errors':list(result.errors)}}

    def prepare_ruling(self,case_ref,admission,basis,result,materials,decision_evidence):
        """One timestamp binds the document and its free market signature."""
        from a2c.codec import digest
        from ..errors import LabError
        if result.get('liable') not in ('client','provider','none') or result.get('rulingKind') not in ('deadline','recompute','review'):
            raise LabError('history_gap','Restore the original complete evaluation',status=409)
        if result['rulingKind']=='review':
            if not isinstance(result.get('operator'),str) or not isinstance(result.get('reason'),str) or not result['reason'].strip():
                raise LabError('history_gap','Restore the independent original review',status=409)
        elif result['liable']=='none':raise LabError('history_gap','Only independent review can be neutral',status=409)
        at=self.now()
        ruling={'kind':'ruling','caseRef':case_ref,'admissionId':admission,'subject':basis['subject'],
            'ground':basis['ground'],'basisDigest':digest(basis),'materialsDigest':digest(materials),
            'rulingKind':result['rulingKind'],'liable':result['liable'],'evidence':result['evidence'],
            'decisionEvidenceDigest':digest(decision_evidence),'issuedAt':fmt_time(at)}
        if result['rulingKind']=='review':
            ruling.update(operator=result['operator'],reason=result['reason'])
            if 'compensation' in result:ruling['compensation']=result['compensation']
        if basis['period'] is not None:ruling['period']=basis['period']
        if basis['subject']['kind']=='contract':ruling['cardDigest']=basis['subject']['digest']
        ruling['signature']=_sign_free(self.signer,ruling,at)
        return ruling

    def commit_ruling_in(self,conn,basis,ruling):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Ruling needs the owned application transaction')
        if basis['subject']['kind']=='contract':
            from ..errors import LabError
            prior=self.ruling_for(basis['subject']['digest'])
            if prior is not None:raise LabError('history_gap','Do not overwrite an original market ruling',status=409)
            outcome=self.contract_outcome(basis['subject']['digest'])
            if outcome is not None:
                if outcome['outcome'] not in ('completed','client-terminated') or ruling['liable']!='none':
                    raise LabError('revision_conflict','Preserve the original market conclusion',status=409)
                return
            self.market.record_ruling(basis['card'],ruling)
            conn.execute('INSERT INTO rulings(card_digest,period,at,ruling) VALUES(?,?,?,?)',
                (basis['subject']['digest'],basis['period'],self.now().timestamp(),jdump(ruling)))

    def ruling_for(self,reference):
        return self.market.store.get('rulings',reference)

    def contract_outcome(self,reference):
        value=self.market.store.get('ledger',reference)
        return {'outcome':value['outcome'],'closedAt':value['closedAt']} if value else None

    def ledger(self) -> dict:
        return self.market.store.all("ledger")


def _sign_free(signer: Signer, obj: Mapping[str, Any], ts) -> dict:
    """市场自有文书的签名：对对象（去签名）摘要签 JWS（a2c-slot=market）。"""
    from a2c.jws import sign
    body = {k: v for k, v in obj.items() if k != "signature"}
    return sign(objects.object_digest(body), signer, "market", ts)


__all__ = ["MarketService"]
