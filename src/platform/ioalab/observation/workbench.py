"""Authorized workbench composition over existing facts; no materialized state."""
from ..storage import jload
from ..errors import LabError
from .events import event_view


class WorkbenchQueries:
    def __init__(self,db,*,auth,wallets,contracts,services,discovery,messages,interfaces):
        self.db,self.auth,self.wallets=db,auth,wallets
        self.contract_views,self.service_views,self.discovery=contracts,services,discovery
        self.message_views,self.interface_directory=messages,interfaces

    def summary(self,uid):
        account=self.db.one('SELECT id,username,display_name,kind FROM users WHERE id=?',uid)
        if not account:raise LabError('forbidden','账号不可用',status=401)
        contracts=self.contract_views.list(uid,private=True)
        services=self.service_views.list(uid)
        income=self.contract_views.income(uid);completed=self.contract_views.service_completions(uid)
        intents=self.contract_views.intents(uid)
        for service in services:
            service['orders']=[row for row in contracts if row['serviceAgentId']==service['id']]
            service.update(income.get(service['id'],{'earned':'0.00','commissionEarned':'0.00','proposalFeesEarned':'0.00'}))
            service['completedCount']=completed.get(service['id'],0)
            service['intents']=[row for row in intents if row['serviceAgentId']==service['id']]
        events=self.db.rows('''SELECT * FROM events WHERE sender=? OR receiver=?
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
                'trust':event['detail'].get('trust','platform'),'stale':bool(event['detail'].get('stale',False))})
        return {'account':account,'roles':self.auth.roles(uid),'wallet':str(self.wallets.balance(uid)),
            'identities':self.db.rows('SELECT id,name,role,status FROM agents WHERE owner_id=?',uid),
            'orders':[row for row in contracts if row['owner_id']==uid],
            'intents':[row for row in intents if row['owner_id']==uid],'services':services,'events':safe,
            'categories':self.discovery.categories(),'messages':self.message_views.list(uid),
            'interfaces':self.interface_directory.interfaces(uid,uid)}

    def service_orders(self,uid,sid):
        return [row for row in self.contract_views.list(uid,private=True) if row["serviceAgentId"]==sid]
