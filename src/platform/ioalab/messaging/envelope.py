"""Envelope values shared by the delivery parts: platform sender, terminal states, JSON, time and identifiers."""
from datetime import datetime, timezone
import json
import uuid

from ..errors import LabError

SYSTEM='urn:ioa:platform'
TERMINAL={'completed','rejected','failed','expired'}


def encode(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError,RecursionError) as exc:raise LabError('invalid_input','Invalid JSON value') from exc


def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')


def timestamp(value):
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.utcoffset() is None:raise ValueError()
        return date.timestamp()
    except (AttributeError,TypeError,ValueError,OverflowError) as exc:
        raise LabError('invalid_input','A UTC timestamp with timezone is required') from exc


def identifier(value,name='messageId'):
    try:return str(uuid.UUID(value))
    except (TypeError,ValueError,AttributeError) as exc:raise LabError('invalid_input',name+' must be a UUID') from exc
