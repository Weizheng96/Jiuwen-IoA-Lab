"""Pre-start, credential-preserving configuration edits; no participant database."""
import copy
import re
from pathlib import Path

from .lease import Lease
from .config import RuntimeConfig,read_configuration
from .files import private_json,prepare_home,read_arguments
from .protocol import RuntimeFault,encode


COMMON_FIELDS={'identities','provider','max_model_runs','max_pending_requests','user_browser'}


def inspect_config(path,*,validate=None,host_fields=()):
    config=RuntimeConfig.load(path)
    if validate:validate(config)
    return _public(config,host_fields)


def _public(config,host_fields):
    return {**config.public_summary(),'configuredServices':sorted(config.provider.get('services',{})),
            'hostFields':sorted(config.host),
            'editableFields':['identities','provider.services','max_model_runs','max_pending_requests','user_browser',
                              *('host.'+field for field in sorted(host_fields))]}


def merge_common(document,changes,*,service_fields,host_update=None):
    if (not isinstance(changes,dict) or not changes or len(encode(changes).encode())>131072
            or set(changes)-(COMMON_FIELDS|({'host'} if host_update else set()))):
        raise RuntimeFault('invalid_input','Use only the declared non-credential configuration fields','correct_input')
    updated=copy.deepcopy(document)
    for field in ('max_model_runs','max_pending_requests','user_browser'):
        if field in changes:updated[field]=copy.deepcopy(changes[field])
    if 'identities' in changes:
        profiles=changes['identities']
        if not isinstance(profiles,list) or not profiles:
            raise RuntimeFault('invalid_input','Provide identity profiles to add or update')
        merged={item['key']:item for item in updated['identities']}
        seen=set()
        for profile in profiles:
            if not isinstance(profile,dict) or set(profile)!={'key','role','name'} or not isinstance(profile['key'],str):
                raise RuntimeFault('invalid_input','Identity profiles require key, role and name')
            key=profile['key']
            if key.casefold() in seen:raise RuntimeFault('invalid_input','Identity changes must have distinct keys')
            seen.add(key.casefold())
            if key in merged and merged[key]['role']!=profile['role']:
                raise RuntimeFault('identity_conflict','Keep the original role of an existing identity')
            merged[key]=copy.deepcopy(profile)
        updated['identities']=list(merged.values())
    if 'provider' in changes:
        provider=changes['provider']
        if (not isinstance(provider,dict) or set(provider)!={'services'}
                or not isinstance(provider['services'],dict) or not provider['services']):
            raise RuntimeFault('invalid_input','Provide local service fields keyed by profile')
        services=updated.setdefault('provider',{}).setdefault('services',{})
        for profile,fields in provider['services'].items():
            if not isinstance(fields,dict) or not fields or set(fields)-set(service_fields):
                raise RuntimeFault('invalid_input','Only declared non-resource service fields can be changed')
            services.setdefault(profile,{}).update(copy.deepcopy(fields))
    if 'host' in changes:updated['host']=host_update(updated.get('host',{}),changes['host'])
    return updated


def configure(path,arguments,*,service_fields=(),validate=None,host_update=None,host_fields=()):
    path=Path(path).resolve()
    original=RuntimeConfig.load(path)
    values=read_arguments(arguments,configuration=path,home=original.home)
    if (set(values)!={'expectedDigest','changes'} or not isinstance(values['expectedDigest'],str)
            or not re.fullmatch('sha256:[0-9a-f]{64}',values['expectedDigest'])):
        raise RuntimeFault('invalid_input','Use expectedDigest from runtime.configuration and a changes object')
    # Validate before creating a dedicated directory or taking an instance lease.
    document=read_configuration(path)
    candidate=merge_common(document,values['changes'],service_fields=service_fields,host_update=host_update)
    checked=RuntimeConfig.from_mapping(path,candidate)
    if validate:validate(checked)
    prepare_home(original.home)
    try:lease=Lease(original.home/'client.lock',timeout=0)
    except RuntimeError:
        raise RuntimeFault('not_ready','Safely stop the existing instance before changing its configuration','runtime.stop') from None
    with lease:
        original.require_current()
        # Check actual fields before treating an old digest as a conflict. This is
        # an idempotent local set, not a receipt for a previous business operation.
        changed=candidate!=document
        if changed and values['expectedDigest']!=original.public_summary()['configurationDigest']:
            raise RuntimeFault('configuration_changed','Configuration changed; inspect it before applying new fields','runtime.configuration')
        if changed:
            original.require_current()
            private_json(path,candidate)
        return {**_public(checked,host_fields),'changed':changed}
