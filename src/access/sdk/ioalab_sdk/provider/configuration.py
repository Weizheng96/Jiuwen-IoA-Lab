"""Pure service configuration validation shared by setup and execution."""
import copy
import re

from ..runtime.protocol import RuntimeFault,encode


SERVICE_FIELDS={'instructions','skills','resources','native_tools','max_active_delegations','accepting'}
EDITABLE_SERVICE_FIELDS=SERVICE_FIELDS-{'resources'}


def validate_provider(value,profiles):
    if (not isinstance(value,dict) or set(value)-{'services','require_publication_authorization'}
            or type(value.get('require_publication_authorization',False)) is not bool
            or not isinstance(value.get('services',{}),dict)):
        raise RuntimeFault('invalid_input','Configure provider.services by local service profile')
    services=copy.deepcopy(value.get('services',{}))
    identities={item['key'] for item in profiles if item['role']=='service'}
    for key,config in services.items():
        if (key not in identities or not isinstance(config,dict) or set(config)-SERVICE_FIELDS
                or not isinstance(config.get('instructions'),str) or not config['instructions'].strip()):
            raise RuntimeFault('invalid_input','Each service needs its local profile and professional instructions')
        if (type(config.get('max_active_delegations',1)) is not int
                or not 1<=config.get('max_active_delegations',1)<=1000
                or type(config.get('accepting',True)) is not bool):
            raise RuntimeFault('invalid_input','Configure bounded active delegations and accepting flag')
        skills=config.get('skills',[])
        if (not isinstance(skills,list) or len(skills)>32
                or any(not isinstance(skill,str) or not skill.strip() for skill in skills)
                or len(set(skills))!=len(skills)):
            raise RuntimeFault('invalid_input','Use installed local business skill references')
        if not isinstance(config.get('resources',{}),dict) or len(encode(config.get('resources',{})).encode())>65536:
            raise RuntimeFault('invalid_input','Use bounded local business resource metadata')
        native=config.get('native_tools',{})
        if (not isinstance(native,dict) or len(native)>64
                or any(not isinstance(name,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name)
                       or name=='ask_user' or level not in ('allow','ask','deny') for name,level in native.items())):
            raise RuntimeFault('invalid_input','Use exact installed native tool names and allow, ask or deny permissions')
    return services
