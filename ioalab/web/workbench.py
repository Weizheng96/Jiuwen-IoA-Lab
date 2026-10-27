"""Owned views for personal assistants and individual provider services."""
from fastapi import APIRouter,Depends,HTTPException
from ..store import jload
from ..psp import money


def build_router(platform,current_user,require_admin):
    router=APIRouter()

    def order_view(row):
        row['detail']=jload(row['detail'])
        provider=platform._agent_of_provider(row['provider_id']) or {}
        row['providerName']=provider.get('name',row['provider_id'])
        card=row['detail'].get('card',{})
        row['signatures']={k:[bool(x) for x in v] if isinstance(v,list) else bool(v) for k,v in card.get('signatures',{}).items()}
        row['payments']=platform.db.rows("SELECT at,amount,purpose,payment_id FROM transfers WHERE ref=? AND payer_id=? AND purpose IN ('deposit','installment') ORDER BY id",row['card_digest'],row['owner_id'])
        row['paid']=str(sum((money(p['amount']) for p in row['payments']),money(0)))
        return row

    @router.get('/api/workbench')
    def summary(uid=Depends(current_user)):
        platform.sync_orders()
        account=platform.db.one('SELECT id,username,display_name,kind FROM users WHERE id=?',uid)
        services=platform.db.rows("SELECT id,name,status,meta,definition FROM agents WHERE owner_id=? AND role='service'",uid)
        for service in services:
            definition=jload(service.pop('definition'))
            service['category']=definition.get('category')
            service['url']=jload(service.pop('meta')).get('url')
            rows=platform.db.rows('SELECT * FROM orders WHERE provider_id=? ORDER BY created DESC LIMIT 100',service['url'])
            service['orders']=[order_view(r) for r in rows]
            payments=platform.db.rows("SELECT t.amount FROM transfers t JOIN orders o ON o.card_digest=t.ref WHERE o.provider_id=? AND t.payee_id=? AND t.payer_id=o.owner_id AND t.purpose IN ('deposit','installment')",service['url'],uid)
            service['earned']=str(sum((money(p['amount']) for p in payments),money(0)))
        # Filter on the server, before projecting. Never send a global event stream to a personal browser.
        events=platform.db.rows('''SELECT seq,at,op,card_digest,period,outcome,detail FROM events
            WHERE sender=? OR sender IN (SELECT id FROM agents WHERE owner_id=? AND role='assistant')
            OR card_digest IN (SELECT card_digest FROM orders WHERE owner_id=? OR provider_id IN
                (SELECT json_extract(meta,'$.url') FROM agents WHERE owner_id=? AND role='service'))
            ORDER BY seq DESC LIMIT 240''',uid,uid,uid,uid)
        for event in events:
            detail=jload(event.pop('detail'))
            event['source']=detail.get('source','platform')
            event['trust']=detail.get('trust','platform')
        return {'account':account,'roles':platform.auth.roles(uid),'wallet':str(platform.wallets.balance(uid)),
                'identities':platform.db.rows('SELECT id,name,role,status FROM agents WHERE owner_id=?',uid),
                'orders':[order_view(r) for r in platform.db.rows('SELECT * FROM orders WHERE owner_id=? ORDER BY created DESC LIMIT 100',uid)],
                'services':services,'events':list(reversed(events)),
                'categories':platform.discovery.categories(),
                'messages':platform.messaging.list(uid),'interfaces':platform.messaging.interfaces(uid,uid)}

    @router.get('/api/catalog')
    def catalog(uid=Depends(current_user)):
        rows=platform.db.rows("SELECT id,name,status,definition,meta FROM agents WHERE role='service'")
        return [{'id':r['id'],'name':r['name'],'status':r['status'],
                 'category':jload(r['definition']).get('category'),
                 'description':jload(r['definition']).get('description',''),
                 'url':jload(r['meta']).get('url')} for r in rows]

    @router.get('/api/services/{sid}/orders')
    def orders(sid:str,uid=Depends(current_user)):
        row=platform.db.one("SELECT * FROM agents WHERE id=? AND role='service'",sid)
        if not row or row['owner_id']!=uid: raise HTTPException(403,'Service belongs to another account')
        platform.sync_orders()
        return [order_view(r) for r in platform.db.rows('SELECT * FROM orders WHERE provider_id=? ORDER BY created DESC',jload(row['meta'])['url'])]

    @router.get('/api/admin/messages')
    def all_messages(_=Depends(require_admin)):
        rows=platform.messaging.list('admin',admin=True)
        return [{k:v for k,v in r.items() if k not in ('payload','result')} for r in rows]

    @router.get('/api/admin/messages/{mid}')
    def audit(mid:str,_=Depends(require_admin)):
        row=platform.messaging.get('admin',mid,admin=True)
        platform.bus.emit('message','message.audit',sender='admin',detail={'messageId':mid})
        return row
    return router
