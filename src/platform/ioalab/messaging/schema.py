"""Bounded public message-schema validation, shared by current interface operations."""
from fastapi import HTTPException
from jsonschema import Draft202012Validator,FormatChecker

KEYWORDS={'type','properties','required','additionalProperties','items','enum','minimum','maximum','minLength','maxLength','minItems','maxItems','description','title','format'}


def error(code,message): raise HTTPException(code,message)


def check_schema(schema,depth=0):
    if depth>8 or not isinstance(schema,(dict,bool)): error(400,'Invalid or overly deep schema')
    if isinstance(schema,bool): return
    if set(schema)-KEYWORDS: error(400,'Unsupported schema keyword')
    if schema.get('type') not in (None,'object','array','string','number','integer','boolean','null'): error(400,'Unsupported schema type')
    if schema.get('format') not in (None,'date-time','email'): error(400,'Unsupported schema format')
    if not isinstance(schema.get('properties',{}),dict): error(400,'properties must be an object')
    for child in schema.get('properties',{}).values(): check_schema(child,depth+1)
    for key in ('items','additionalProperties'):
        if key in schema: check_schema(schema[key],depth+1)
    try: Draft202012Validator.check_schema(schema)
    except Exception: error(400,'Invalid JSON Schema')


def validate(schema,value):
    def bounded(item,depth=0):
        if depth>8: error(400,'Message nesting exceeds eight levels')
        if isinstance(item,dict):
            for child in item.values(): bounded(child,depth+1)
        elif isinstance(item,list):
            for child in item: bounded(child,depth+1)
    bounded(value)
    try: problem=next(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value),None)
    except (RecursionError,ValueError,TypeError): error(400,'Invalid message structure')
    if problem: error(400,'Schema mismatch at '+('/'.join(map(str,problem.absolute_path)) or '/'))
