"""Presentation helpers for role-owned workflows; no business routing lives here."""
def form_schema(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties if required is None else required),
            'additionalProperties': False}


def form_text(title, **kwargs): return {'type': 'string', 'title': title, **kwargs}


def form_next(title, fields, context, *, values=None, evidence=None, data=None):
    return {'status': 'completed', 'data': data or {}, 'next': {'title': title, 'schema': fields,
            'context': context, 'values': values or {}, 'evidence': evidence or {}}}
