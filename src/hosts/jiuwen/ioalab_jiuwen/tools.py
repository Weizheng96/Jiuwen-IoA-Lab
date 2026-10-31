"""Explicit local composition of installed business factories, outside the SDK."""
import copy
from importlib.metadata import entry_points
import re

from jsonschema import Draft202012Validator
from ioalab_sdk.runtime.host import BusinessTools,ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault,encode


class ConfiguredTools:
    def __init__(self,specs,profiles,*,home,base_dir):
        self.by_profile={};self.bundles=[]
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
                self.bundles.append(bundle)
                if not isinstance(bundle.bindings,tuple) or len(bundle.bindings)>32 or bundle.close is not None and not callable(bundle.close):
                    raise RuntimeFault('invalid_input','Business tools need bounded bindings and a local close callback')
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

    def close(self):
        bundles,self.bundles=self.bundles,[]
        failures=[]
        for bundle in reversed(bundles):
            if callable(bundle.close):
                try:bundle.close()
                except Exception as exc:failures.append(type(exc).__name__)
        if failures:raise RuntimeFault('not_ready','Business tool resource close failed: '+','.join(failures),'inspect_business_resources')
