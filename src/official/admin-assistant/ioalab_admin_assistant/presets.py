"""Only package-owned declarations can receive initial deployment permission."""
import copy
from importlib.resources import files
import json

from a2c.codec import digest_bytes
from ioalab_sdk.assistant.rules import parse, render
from ioalab_sdk.runtime.protocol import RuntimeFault


def load(*, meetings):
    raw = files(__package__).joinpath('presets.json').read_bytes()
    value = json.loads(raw)
    if not isinstance(value, list) or [item['declaration']['name'] for item in value] != ['message.forward', 'meeting.request']:
        raise RuntimeFault('not_ready', 'Restore the original packaged administrator presets')
    result = copy.deepcopy(value)
    for item in result:
        if item['declaration']['name'] == 'meeting.request' and not meetings:
            item['declaration'].update(enabled=False, tools=[])
        parse(render(item['declaration'], item['instructions']))
    return digest_bytes(raw), result
