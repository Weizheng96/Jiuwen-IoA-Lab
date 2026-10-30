"""Small transport values, independent of the platform and agent host."""
import json
from datetime import datetime, timezone


class RuntimeFault(Exception):
    def __init__(self, code, message, recovery='inspect', *, operation_id=None):
        super().__init__(message)
        self.code, self.recovery, self.operation_id = code, recovery, operation_id


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
