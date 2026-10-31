"""Authorized browser views; reading never consumes participant messages or signs a card."""
from fastapi import APIRouter,Depends,HTTPException

from ..store import jload
from ..observation import event_view


def build_router(platform,current_user,require_admin,require_auditor):
    router=APIRouter()

    @router.get('/api/workbench')
    def summary(uid=Depends(current_user)):
        account=platform.db.one('SELECT id,username,display_name,kind FROM users WHERE id=?',uid)
        if not account:raise HTTPException(401,'账号不可用')
        contracts=platform.contract_views.list(uid,private=True)
        services=platform.service_views.list(uid)
        income=platform.contract_views.income(uid);completed=platform.contract_views.service_completions(uid)
        intents=platform.contract_views.intents(uid)
        for service in services:
            service['orders']=[row for row in contracts if row['serviceAgentId']==service['id']]
            service.update(income.get(service['id'],{'earned':'0.00','commissionEarned':'0.00','proposalFeesEarned':'0.00'}))
            service['completedCount']=completed.get(service['id'],0)
            service['intents']=[row for row in intents if row['serviceAgentId']==service['id']]
        events=platform.db.rows('''SELECT * FROM events WHERE sender=? OR receiver=?
            OR sender IN (SELECT id FROM agents WHERE owner_id=?)
            OR receiver IN (SELECT id FROM agents WHERE owner_id=?)
            OR card_digest IN (SELECT v.digest FROM observation_contracts v JOIN delegation_admissions a ON a.id=v.admission
                JOIN agents c ON c.id=a.client JOIN agents s ON s.id=a.service WHERE c.owner_id=? OR s.owner_id=?)
            ORDER BY seq DESC LIMIT 240''',uid,uid,uid,uid,uid,uid)
        safe=[]
        for row in reversed(events):
            row['detail']=jload(row['detail']);event=event_view(row)
            safe.append({'seq':event['seq'],'at':event['at'],'op':event['op'],'card_digest':event['cardDigest'],
                'period':event['period'],'outcome':event['outcome'],'source':event['detail'].get('source','platform'),
                'trust':event['detail'].get('trust','platform')})
        return {'account':account,'roles':platform.auth.roles(uid),'wallet':str(platform.wallets.balance(uid)),
            'identities':platform.db.rows('SELECT id,name,role,status FROM agents WHERE owner_id=?',uid),
            'orders':[row for row in contracts if row['owner_id']==uid],
            'intents':[row for row in intents if row['owner_id']==uid],'services':services,'events':safe,
            'categories':platform.discovery.categories(),'messages':platform.message_views.list(uid),
            'interfaces':platform.messaging.interfaces(uid,uid)}

    @router.get('/api/catalog')
    def catalog(uid=Depends(current_user)):return platform.service_views.list()

    @router.get('/api/services/{sid}/orders')
    def orders(sid:str,uid=Depends(current_user)):
        service=platform.identities.owned(uid,sid)
        if service['role']!='service':raise HTTPException(403,'请选择本账号服务')
        return [row for row in platform.contract_views.list(uid,private=True) if row['serviceAgentId']==sid]

    @router.get('/api/admin/messages')
    def all_messages(operator=Depends(require_admin)):
        return [{**row,'canAudit':platform.auth.is_auditor(operator)} for row in platform.message_views.list()]

    @router.get('/api/admin/messages/{mid}')
    def audit(mid:str,operator=Depends(require_auditor)):
        row=platform.message_views.detail(mid)
        if not row:raise HTTPException(404,'消息不可用')
        platform.bus.emit('message','message.audit',sender=operator,
            detail={'messageId':mid,'interactionId':row['interactionId'],'source':'web_audit'})
        return row

    return router
