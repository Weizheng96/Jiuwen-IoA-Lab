"""Explicit one-time import of a completed installation's literal core environment."""
import argparse
import hashlib
import json
from pathlib import Path

from .configuration import validate
from .layout import read_environment,write_initial
from .release import digest,read_json
from .workspace import save,path_without_links


def imported(environment,template):
    value=json.loads(json.dumps(template))
    runtime=value['runtime'];llm=value['llm']
    names={'public_url':'PUBLIC_URL','session_secret':'SECRET','quota_needs_per_day':'NEEDS_PER_DAY'}
    consumed=set()
    for key,default in runtime.items():
        name='IOALAB_'+names.get(key,key.upper())
        if name not in environment:continue
        raw=environment[name];consumed.add(name)
        if isinstance(default,bool):
            if raw not in ('0','1'):raise ValueError('Invalid original boolean configuration')
            runtime[key]=raw=='1'
        elif isinstance(default,int):runtime[key]=int(raw)
        else:runtime[key]=None if key=='seed_file' and not raw else raw
    for key,suffix in {'api_key':'LLM_API_KEY','base_url':'LLM_BASE_URL','fallback_base':'LLM_BASE_URL_FALLBACK',
            'model':'LLM_MODEL','discovery_model':'A2X_MODEL','embedding_model':'EMBED_MODEL',
            'concurrency':'LLM_CONCURRENCY','round1_attempts':'ROUND1_ATTEMPTS','arbitration_models':'ARBITRATION_MODELS'}.items():
        name='IOALAB_'+suffix
        if name in environment:
            raw=environment[name];consumed.add(name)
            llm[key]=int(raw) if isinstance(llm[key],int) else json.loads(raw) if isinstance(llm[key],dict) else raw
    for group,key,name in [('admin','password','IOALAB_ADMIN_PASSWORD'),('admin','username','IOALAB_ADMIN_USERNAME'),
                           ('credits','login','IOALAB_LOGIN_CREDIT')]:
        if name in environment:value[group][key]=environment[name];consumed.add(name)
    unknown={name for name in environment if name.startswith('IOALAB_')}-consumed
    if unknown:raise ValueError('Review unsupported original platform configuration fields')
    if not value['admin']['password'] or not runtime['session_secret']:
        raise ValueError('Restore the original administrator and session credentials before importing')
    return value


def migrate(layout):
    identity=layout.inspect()
    from .jobs import Job
    pointer=read_json(layout.private/'current-job.json')
    if Job(layout,pointer['operationId']).observe()['status']!='completed':
        raise RuntimeError('Complete the original installation before configuration migration')
    original=path_without_links(layout.private/'initial.json');archive=path_without_links(layout.private/'initial.before-platform-config.json')
    marker=path_without_links(layout.private/'configuration-migration.json')
    old=read_json(archive if archive.exists() else original)
    if old.get('deploymentId')!=identity['deploymentId']:raise ValueError('Original deployment identity differs')
    if 'platform_config' in old.get('input',{}):raise ValueError('This deployment already uses platform configuration')
    environment_path=path_without_links(layout.config/'core/core.env')
    environment=read_environment(environment_path)
    value=imported(environment,read_json(Path(__file__).with_name('platform.json.example')))
    target=layout.config/'core/platform.json'
    intent={'deploymentId':identity['deploymentId'],'environmentSha256':hashlib.sha256(environment_path.read_bytes()).hexdigest(),
            'platformDigest':digest(value),'platformConfig':str(target)}
    if marker.exists() and read_json(marker)!=intent:raise ValueError('Continue the original unchanged configuration import')
    write_initial(marker,(json.dumps(intent,sort_keys=True)+'\n').encode())
    write_initial(archive,original.read_bytes() if not archive.exists() else archive.read_bytes())
    write_initial(target,(json.dumps(value,ensure_ascii=False,sort_keys=True)+'\n').encode())
    options={key:old['input'][key] for key in ('pypi_index','max_model_runs','admin_assistant','swarm_ports') if key in old['input']}
    options=validate({'platform_config':str(target),**options})
    files={key:entry for key,entry in old['files'].items() if Path(key)!=environment_path}
    files[str(target)]={'phase':'ready'}
    new={'format':old['format'],'deploymentId':identity['deploymentId'],'inputDigest':digest(options),'input':options,'files':files}
    current=read_json(original)
    if current not in (old,new):raise ValueError('Installation input changed during configuration migration')
    save(original,new)
    return {'status':'completed','platformConfig':str(target),'preservedEnvironment':str(environment_path),
            'preservedInitialInput':str(archive),'next':'upgrade_with_same_platform_config_reference'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args(argv)
    from .__main__ import existing
    try:
        layout=existing(args.state)
        from .environment import writer
        with writer(layout.private/'launcher.lock'):result=migrate(layout)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as error:
        print(json.dumps({'status':'not_ready','error':{'type':type(error).__name__}},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
