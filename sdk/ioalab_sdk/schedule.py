"""Editable participant-owned availability, without an external calendar account."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from .lease import Lease

DEFAULT_RULES = {"weekdays": [0, 1, 2, 3, 4], "startHour": 9, "endHour": 18,
                 "minMinutes": 15, "maxMinutes": 120, "leadMinutes": 30, "daysAhead": 30}
RULES_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": list(DEFAULT_RULES), "properties": {
        "weekdays": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}, "minItems": 1, "maxItems": 7, "uniqueItems": True},
        "startHour": {"type": "integer", "minimum": 0, "maximum": 22},
        "endHour": {"type": "integer", "minimum": 1, "maximum": 23},
        "minMinutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        "maxMinutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        "leadMinutes": {"type": "integer", "minimum": 0, "maximum": 525600},
        "daysAhead": {"type": "integer", "minimum": 1, "maximum": 365}}}
SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["timeZone", "rules", "events"], "properties": {
        "timeZone": {"type": "string", "minLength": 1, "maxLength": 64},
        "rules": RULES_SCHEMA,
        "events": {"type": "array", "maxItems": 1000, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "title", "start", "end"],
            "properties": {"id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,100}$"},
                "title": {"type": "string", "maxLength": 200},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "status": {"enum": ["busy", "cancelled"]}}}}}}


def instant(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Schedule times require an explicit UTC offset")
    return value


def validate(document):
    try:
        Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(document)
    except ValidationError:
        # A receiver error can become a message result; never include private event contents.
        raise ValueError("Invalid local schedule structure") from None
    ZoneInfo(document["timeZone"])
    rules = document["rules"]
    if rules["startHour"] >= rules["endHour"] or rules["minMinutes"] > rules["maxMinutes"]:
        raise ValueError("Schedule rule bounds are reversed")
    seen = set()
    for event in document["events"]:
        if event["id"] in seen:
            raise ValueError("Schedule event IDs must be unique")
        seen.add(event["id"])
        if instant(event["end"]) <= instant(event["start"]):
            raise ValueError("Schedule event end must be after start")
    return document


class ScheduleSnapshot:
    def __init__(self, owner, raw):
        self.owner = owner
        self.revision = hashlib.sha256(raw).hexdigest()
        self.document = validate(json.loads(raw.decode("utf-8-sig")))
        self.rules = {"timeZone": self.document["timeZone"], **self.document["rules"]}

    def busy(self, start, end):
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("Appointment times require offsets and a positive duration")
        return any(event.get("status", "busy") != "cancelled"
                   and instant(event["start"]) < end and start < instant(event["end"])
                   for event in self.document["events"])

    def assert_current(self):
        if hashlib.sha256(self.owner.raw()).hexdigest() != self.revision:
            raise ValueError("Schedule changed during processing; no invitation submitted")


class LocalSchedule:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock = self.path.with_name(self.path.name + ".lock")

    def raw(self):
        with self.path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("Schedule exceeds 256 KiB")
        return raw

    def read(self):
        return ScheduleSnapshot(self, self.raw())

    @contextmanager
    def session(self):
        # Shared with SDK edits; keep one set of rules throughout the decision.
        with Lease(self.lock):
            yield self.read()

    def _atomic(self, path, raw):
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".schedule-", delete=False) as file:
                name = Path(file.name)
                file.write(raw);file.flush();os.fsync(file.fileno())
            name.chmod(0o600)
            os.replace(name, path)
        finally:
            if name is not None and name.exists():name.unlink()

    @staticmethod
    def encode(document):
        validate(document)
        raw = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(raw) > 256 * 1024:raise ValueError("Schedule exceeds 256 KiB")
        return raw

    def init(self):
        raw = self.encode({"timeZone": "Asia/Shanghai", "rules": DEFAULT_RULES, "events": []})
        with Lease(self.lock):
            if self.path.exists():raise FileExistsError("Schedule already exists; use show and replace")
            self._atomic(self.path, raw)
        return self.show()

    def show(self):
        snapshot = self.read()
        return {"source": "local-schedule", "path": str(self.path), "revision": snapshot.revision,
                "document": snapshot.document, "scope": "Own local availability; no private Outlook calendar read"}

    def replace(self, document, revision):
        raw = self.encode(document)
        with Lease(self.lock):
            old = self.raw()
            if not isinstance(revision, str) or hashlib.sha256(old).hexdigest() != revision:
                raise ValueError("Schedule revision conflict; read again before editing")
            self._atomic(self.path.with_name(self.path.name + ".bak"), old)
            self._atomic(self.path, raw)
        return self.show()


def configured(home):
    return LocalSchedule(os.getenv("IOALAB_SCHEDULE_FILE") or Path(home) / "schedule.json")
