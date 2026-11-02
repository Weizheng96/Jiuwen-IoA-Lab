"""Jiuwen-owned, side-effect-free setup for existing public Process hosts."""
import copy

from ioalab_sdk.runtime.protocol import RuntimeFault
from .process import ProcessHost


HOST_FIELDS={'command','data_dir','model','skill_directories'}


def validate_host(config,*,base_dir):
    if config.host:
        value=dict(config.host);value.pop('business_tools',None)
        ProcessHost.from_config(value,base_dir=base_dir)


def merge_host(current,changes):
    if not isinstance(changes,dict) or not changes or set(changes)-HOST_FIELDS:
        raise RuntimeFault('invalid_input','Only public Process host fields can be configured; tool options stay private')
    updated=copy.deepcopy(current)
    for key,value in changes.items():
        if key=='skill_directories':
            if not isinstance(value,dict) or not value:
                raise RuntimeFault('invalid_input','Provide exact skill names and absolute local directories')
            updated.setdefault(key,{}).update(value)
        else:updated[key]=copy.deepcopy(value)
    return updated
