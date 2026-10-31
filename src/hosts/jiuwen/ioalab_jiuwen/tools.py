"""Explicit local composition of installed business factories, outside the SDK."""
import copy
from importlib.metadata import entry_points
import re

from jsonschema import Draft202012Validator
from ioalab_sdk.runtime.host import BusinessTools,ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault,encode


class ConfiguredTools:
    def __init__(self,specs,profiles,*,home,base_dir):
        self.by_profile={};self.bundles=[];self.labels=[];self._started=False
        if not isinstance(specs,list) or len(specs)>32:
            raise RuntimeFault('invalid_input','host.business_tools must list up to32 local installed factories')
        identities={profile['key']:profile for profile in profiles}
        entries={}
        if specs:
            for entry in entry_points(group='ioalab.business_tools'):
                entries.setdefault(entry.name,[]).append(entry)
        try:
            for spec in specs:
                if not isinstance(spec,dict) or set(spec)!={'factory','profile','options'}:
                    raise RuntimeFault('invalid_input','Business tools require factory, profile and options')
                factory,profile=spec['factory'],spec['profile']
                if not isinstance(factory,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}',factory):
                    raise RuntimeFault('invalid_input','Use an installed business factory name, not an import path')
                if not isinstance(profile,str) or profile not in identities or not isinstance(spec['options'],dict) or len(encode(spec['options']).encode())>65536:
                    raise RuntimeFault('invalid_input','Select an owned local profile and bounded business options')
                if len(entries.get(factory,()))!=1:
                    raise RuntimeFault('not_ready','Install exactly one selected business factory: '+factory,'install_business_tools')
                try:bundle=entries[factory][0].load()(copy.deepcopy(spec['options']),home=home,profile=profile,base_dir=base_dir)
                except RuntimeFault:raise
                except Exception as exc:
                    raise RuntimeFault('not_ready','Business factory configuration failed: '+factory+' ('+type(exc).__name__+')','configure_business_tools') from None
                if not isinstance(bundle,BusinessTools):
                    raise RuntimeFault('invalid_input','Business factory must return SDK BusinessTools')
                self.bundles.append(bundle);self.labels.append({'factory':factory,'profile':profile})
                if (not isinstance(bundle.bindings,tuple) or len(bundle.bindings)>32 or
                    any(callback is not None and not callable(callback) for callback in (bundle.start,bundle.status,bundle.close))):
                    raise RuntimeFault('invalid_input','Business tools need bounded bindings and local lifecycle callbacks')
                tools=self.by_profile.setdefault(profile,{})
                for tool in bundle.bindings:
                    if (not isinstance(tool,ToolBinding) or not re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,127}',tool.name) or
                        tool.name in tools or tool.name in ('ask_user','ioa_reply') or tool.name.startswith('provider.task.') or
                        not callable(tool.invoke) or not isinstance(tool.description,str) or not 1<=len(tool.description)<=4000 or
                        not isinstance(tool.revision,str) or not 1<=len(tool.revision)<=256 or
                        not isinstance(tool.effect,str) or not tool.effect or not isinstance(tool.requires_steps,tuple) or
                        len(tool.requires_steps)>32 or any(not isinstance(step,str) or not step for step in tool.requires_steps)):
                        raise RuntimeFault('invalid_input','Business tool binding is invalid, duplicated or reserved')
                    if identities[profile]['role']=='assistant' and tool.requires_steps:
                        raise RuntimeFault('invalid_input','A2C step requirements belong to service tools')
                    Draft202012Validator.check_schema(tool.input_schema)
                    if tool.input_schema.get('type')!='object' or len(encode(tool.policy()).encode())>32768:
                        raise RuntimeFault('invalid_input','Business tools require bounded object inputs')
                    tools[tool.name]=tool
        except BaseException:
            try:self.close()
            except Exception:pass  # Keep the original configuration failure.
            raise

    def for_profile(self,profile):return dict(self.by_profile.get(profile,{}))

    def notify(self,message,owned):
        if not isinstance(message,dict) or set(message)!={'input','context'} or not isinstance(message['context'],dict):
            raise RuntimeFault('invalid_input','Use SDK notification input and fixed source context')
        tool=owned.get(message['context'].get('agentId'))
        if tool is None:raise RuntimeFault('forbidden','Notification identity has no configured owner channel')
        return tool.invoke(message['input'],message['context'])

    def start(self):
        if self._started:return
        for bundle in self.bundles:
            if bundle.start:bundle.start()
        self._started=True

    def status(self):
        values=[]
        for label,bundle in zip(self.labels,self.bundles):
            try:
                value=bundle.status() if self._started and bundle.status else {'state':'ready' if self._started else 'not_started'}
                if not isinstance(value,dict) or len(encode(value).encode())>8192:raise ValueError('Invalid resource status')
                values.append({**label,'resource':copy.deepcopy(value)})
            except Exception:
                values.append({**label,'resource':{'state':'error','code':'resource_status_unavailable'}})
        return values

    def close(self):
        failed=[];failures=[]
        for label,bundle in reversed(list(zip(self.labels,self.bundles))):
            try:
                if bundle.close:bundle.close()
            except Exception as exc:
                failed.append((label,bundle));failures.append(type(exc).__name__)
        self.labels=[label for label,bundle in reversed(failed)]
        self.bundles=[bundle for label,bundle in reversed(failed)]
        self._started=self._started and bool(failed)
        if failures:raise RuntimeFault('not_ready','Business tool resource close failed: '+','.join(failures),'inspect_business_resources')
