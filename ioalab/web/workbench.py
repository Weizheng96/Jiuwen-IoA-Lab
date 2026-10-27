"""Owned views for personal assistants and individual provider services."""
from fastapi import APIRouter,Depends,HTTPException
from ..store import jload


def build_router(platform,current_user,require_admin):
    router=APIRouter()

    def order_view(row):
        row['detail']=jload(row['detail'])
        provider=platform._agent_of_provider(row['provider_id']) or {}
        row['providerName']=provider.get('name',row['provider_id'])
        card=row['detail'].get('card',{})
        row['signatures']={k:[bool(x) for x in v] if isinstance(v,list) else bool(v) for k,v in card.get('signatures',{}).items()}
        return row

    @router.get('/api/workbench')
    def summary(uid=Depends(current_user)):
        platform.sync_orders()
        account=platform.db.one('SELECT id,username,display_name,kind FROM users WHERE id=?',uid)
        services=platform.db.rows("SELECT id,name,status,meta FROM agents WHERE owner_id=? AND role='service'",uid)
        for service in services:
            service['url']=jload(service.pop('meta')).get('url')
            rows=platform.db.rows('SELECT * FROM orders WHERE provider_id=? ORDER BY created DESC LIMIT 100',service['url'])
            service['orders']=[order_view(r) for r in rows]
        return {'account':account,'roles':platform.auth.roles(uid),'wallet':str(platform.wallets.balance(uid)),
                'identities':platform.db.rows('SELECT id,name,role,status FROM agents WHERE owner_id=?',uid),
                'orders':[order_view(r) for r in platform.db.rows('SELECT * FROM orders WHERE owner_id=? ORDER BY created DESC LIMIT 100',uid)],
                'services':services,'messages':platform.messaging.list(uid),'interfaces':platform.messaging.interfaces(uid,uid)}

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
