"""Side-effect-free Codex configuration, with no Jiuwen dependency."""
import copy
from ioalab_sdk.runtime import RuntimeFault
from .host import CodexHost

HOST_FIELDS = {'command', 'model', 'skill_directories'}


def validate_host(config, *, base_dir):
    value = dict(config.host)
    value.pop('type', None)
    return CodexHost.from_config(value, base_dir=base_dir)


def merge_host(current, changes):
    if not isinstance(changes, dict) or not changes or set(changes) - HOST_FIELDS:
        raise RuntimeFault('invalid_input', 'Codex configuration accepts command, model and skill_directories')
    updated = copy.deepcopy(current)
    for key, value in changes.items():
        if key == 'skill_directories':
            if not isinstance(value, dict) or not value:
                raise RuntimeFault('invalid_input', 'Provide exact skill names and absolute directories')
            updated.setdefault(key, {}).update(copy.deepcopy(value))
        else:
            updated[key] = copy.deepcopy(value)
    return updated
