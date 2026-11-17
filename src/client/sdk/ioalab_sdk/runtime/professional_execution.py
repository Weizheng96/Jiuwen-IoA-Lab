"""Original professional documents and P26 I/O for one P20 submission.

No discovery, persistent effect journal, model scheduling or host method lives here.
"""
import threading

from ioalab_sdk.runtime.definition_snapshots import read_definition_resource
from ioalab_sdk.runtime.host_submission import HostResource, HostToolDeclaration
from ioalab_sdk.runtime.protocol import RuntimeFault


READER_NAME='ioa_read_business_skill'
READER_INPUT={'type':'object','properties':{'skill':{'type':'string'},'path':{'type':'string'},
    'resource_id':{'type':'string'}},'oneOf':[{'required':['skill'],'not':{'required':['resource_id']}},
    {'required':['resource_id'],'not':{'anyOf':[{'required':['skill']},{'required':['path']}]}}],
    'additionalProperties':False}
READER_OUTPUT={'type':'object','properties':{name:{'type':'string'} for name in
    ('content','digest','resource_id','skill','path')},'required':['content','digest','resource_id'],
    'additionalProperties':False}


class ProfessionalExecution:
    def __init__(self, *, definition, business_binding, workspace, tools,
                 authorize_definition, period_resources, material_resources=()):
        tools,material_resources=tuple(tools),tuple(material_resources)
        self.definition=definition
        self.binding,self.workspace=business_binding,workspace
        self.authorize_definition=authorize_definition
        self.period_resources=period_resources
        self.definitions={item.resource_id:item for item in definition.resources}
        self.materials={item.resource_id:item for item in material_resources}
        if (len(self.definitions)!=len(definition.resources) or
            len(self.materials)!=len(material_resources) or self.definitions.keys() & self.materials.keys()):
            raise RuntimeFault('identity_conflict','Original resource ownership is ambiguous')
        if not callable(authorize_definition):
            raise RuntimeFault('forbidden','Use the original definition authorization fence')
        if any(item.name==READER_NAME for item in tools):
            raise RuntimeFault('invalid_input','Original business tools conflict with the SDK document reader')
        self.aliases={}
        self.required=set()
        selected_skills={item.name.split('/',1)[0] for item in definition.resources
                         if '/' in item.name and item.name.split('/',1)[1]=='SKILL.md'}
        for item in definition.resources:
            parts=item.name.split('/',1)
            if len(parts)==2 and parts[0] in selected_skills and (parts[1]=='SKILL.md' or parts[1].startswith('references/')):
                key=tuple(parts)
                if key in self.aliases:raise RuntimeFault('identity_conflict','Ambiguous professional document name')
                self.aliases[key]=item
                if parts[1]=='SKILL.md':self.required.add(item.resource_id)
            else:
                # A standalone document is not mislabeled as an installed SKILL.md.
                self.required.add(item.resource_id)
        reader=(HostToolDeclaration(READER_NAME,'Read original selected professional documents and exact digests',
                                   READER_INPUT,READER_OUTPUT),) if definition.resources else ()
        self.tools=tuple(tools)+reader
        self.resource_io=(self.read_resource,period_resources.store_output)

    def read_resource(self, resource):
        if not isinstance(resource,HostResource):
            raise RuntimeFault('forbidden','Use an original SDK resource reference')
        if self.definitions.get(resource.resource_id)==resource:
            self.authorize_definition(resource)
            return read_definition_resource(self.definition,resource,binding=self.binding,workspace=self.workspace)
        if self.materials.get(resource.resource_id)==resource:
            return self.period_resources.read_resource(resource)
        raise RuntimeFault('forbidden','Resource is outside this original execution')

    def _select(self, arguments):
        if set(arguments)=={'resource_id'}:
            resource=self.definitions.get(arguments['resource_id'])
        elif 'skill' in arguments and not set(arguments)-{'skill','path'}:
            resource=self.aliases.get((arguments['skill'],arguments.get('path','SKILL.md')))
        else:resource=None
        if resource is None:raise RuntimeFault('forbidden','Document is outside the selected professional snapshot')
        return resource

    def wrap(self, original_callback):
        """Fresh actual-read evidence per submission, never an effect/restart journal."""
        read=set();lock=threading.Lock()
        def on_tool(call):
            if call.name==READER_NAME:
                resource=self._select(call.arguments)
                data=self.read_resource(resource)
                try:content=data.decode('utf-8')
                except UnicodeError:
                    raise RuntimeFault('invalid_tool_output','Original professional document is not UTF-8') from None
                result={'content':content,'digest':resource.digest,'resource_id':resource.resource_id}
                for (skill,path),selected in self.aliases.items():
                    if selected==resource:result.update(skill=skill,path=path);break
                with lock:read.add(resource.resource_id)
                return result
            with lock:ready=self.required.issubset(read)
            if not ready:
                raise RuntimeFault('read_business_skill','Read the original selected professional document before SDK business actions')
            return original_callback(call)
        return on_tool
