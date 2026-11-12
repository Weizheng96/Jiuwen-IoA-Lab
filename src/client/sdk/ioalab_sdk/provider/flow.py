"""Service management composes the original publication and task owners."""
from ..runtime import FlowJournal, RuntimeFault


class ProviderFlows:
    ACTIONS = ('publish', 'pause', 'resume_admission', 'status', 'tasks', 'recover_task', 'template', 'verifier', 'categories', 'reputation', 'dispute', 'dispute_status')

    def __init__(self, role):
        self.role = role
        self.journals = {profile: FlowJournal(role.runtime, identity.agent_id, 'provider', self.advance)
                         for profile, identity in role.identities.items()}

    def call(self, operation, args):
        self.role._identity(args['profile'])
        return self.journals[args['profile']].call(operation,args,self.ACTIONS)

    def advance(self, journal, r):
        if not r['active']:
            return r.get('receipt') or journal.receipt(r)
        profile = next(p for p,i in self.role.identities.items() if i.agent_id == journal.actor)
        action, args = r['action'], r['input']
        invoke = lambda name, operation, value: self.role._handler(name.split('.',1)[1])(self.role.runtime,operation,value)
        def step(index, name, value):
            return journal.step(r,index,'provider.'+name,value,invoke)
        if action == 'publish':
            if set(args) != {'service'} or not isinstance(args['service'],dict) or args['service'].get('profile') != profile:
                raise RuntimeFault('invalid_input','Provide the service owned by this profile')
            service = args['service']
            terms = service.get('terms')
            if (set(service)-{'profile','name','description','tags','categoryId','terms'} or
                not isinstance(terms,list) or not 1 <= len(terms) <= 16):
                raise RuntimeFault('invalid_input','Provide service business fields and one to 16 terms objects; the flow prepares unsigned templates')
            templates = [step(i,'template.draft',{'profile':profile,'terms':value})['template'] for i,value in enumerate(terms)]
            prepared = {k:v for k,v in service.items() if k != 'terms'}
            prepared['templates'] = templates
            preview = step(len(terms),'preview',{'service':prepared})
            if preview.get('readyToPublish') is False:
                r['active'] = False
                return journal.receipt(r,reason='business_choice',required=('/service/categoryId',),result=preview)
            result = step(len(terms)+1,'publish',{key:preview[key] for key in ('service','contentDigest','expectedRevision')})
        else:
            names = {'tasks':'tasks.get','recover_task':'task.resume','template':'template.draft','verifier':'verifier.publish','resume_admission':'resume'}
            if 'profile' in args:
                raise RuntimeFault('invalid_input','The flow supplies its original profile')
            result = step(0,names.get(action,action),args if action=='categories' else {'profile':profile,**args})
        r['active'] = False
        return journal.receipt(r,'completed','business_input',result=result)

    def scan(self):
        for journal in self.journals.values():
            journal.scan()
