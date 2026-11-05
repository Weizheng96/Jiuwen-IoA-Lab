"""Default Linux deployment: submit/observe one detached installation task."""
import argparse
import json
from pathlib import Path
import stat
import uuid

from . import activation
from .configuration import load
from .configs import Configuration
from .environment import writer
from .jobs import Job,source_identity
from .layout import Layout,MARKER
from .linux import require_host,Systemd,regular
from .release import read_json
from .workspace import path_without_links,save


def existing(state):
    state=path_without_links(state);value=read_json(regular(state/MARKER))
    if value.get('state')!=str(state):raise ValueError('Use the original recorded deployment state directory')
    layout=Layout(value['root'],state,value['config'],core_user=value['users']['core'],agents_user=value['users']['agents'])
    if layout.inspect()!=value:raise ValueError('Restore the original shared deployment identity')
    return layout


def status(layout):
    metadata=layout.private/'installation.json'
    value=read_json(regular(metadata)) if metadata.exists() else {'units':{}}
    manager=Systemd(layout.inspect()['deploymentId'],value['units'],lambda:None)
    units={}
    for name,entry in value['units'].items():
        try:
            live=manager.owned(name)
            units[name]={'active':live.get('ActiveState'),'substate':live.get('SubState'),
                         'enabled':live.get('UnitFileState'),'filePhase':entry.get('phase')}
        except (RuntimeError,ValueError):
            units[name]={'ownership':'unverified','filePhase':entry.get('phase'),
                         'recovery':'inspect_original_unit_and_record'}
    pointer=layout.private/'current-job.json'
    task=Job(layout,read_json(regular(pointer))['operationId']).observe() if pointer.exists() else {'status':'not_started'}
    current=activation.current(layout)
    result={'status':'completed','deploymentId':layout.inspect()['deploymentId'],'task':task,
        'currentSet':current['setId'] if current else None,'units':units,
        'lastReadiness':value.get('lastReadiness'),'lastVerifiedAt':value.get('verifiedAt')}
    if value.get('initialized'):
        from . import lifecycle
        result['runtime']=lifecycle.status(layout)
    return result


def submit(args):
    value=load(args.config)
    if stat.S_IMODE(args.config.stat().st_mode)&0o077:
        raise ValueError('Protect the initial deployment JSON with private owner-only permissions')
    source=Path(__file__).resolve().parent.parent
    layout=Layout(args.root,args.state,args.config_dir,
                  core_user=getattr(args,'core_user','ioalab-core'),agents_user=getattr(args,'agents_user','ioalab-agents'))
    if any(path.is_relative_to(source) or source.is_relative_to(path) for path in (layout.root,layout.state,layout.config)):
        raise ValueError('Source, runtime, state and configuration roots must be disjoint')
    identity=layout.claim()
    with writer(layout.private/'launcher.lock'):
        configuration=Configuration(layout,identity,value)
        snapshot=source_identity(source)
        pointer=layout.private/'current-job.json'
        selected=None
        if pointer.exists():
            previous=read_json(regular(pointer));selected=Job(layout,previous['operationId'])
            if (previous['action'],previous['source'],previous['inputDigest'])!=(args.command,snapshot,configuration.owner['inputDigest']):
                if selected.observe()['status']!='completed':
                    raise RuntimeError('The original installation is unfinished; observe/resume it before another source or action')
                selected=None
        if selected is None:
            entry={'operationId':uuid.uuid4().hex,'action':args.command,'source':snapshot,
                   'inputDigest':configuration.owner['inputDigest']}
            save(pointer,entry);selected=Job(layout,entry['operationId'])
        else:entry=read_json(pointer)
        selected.create(entry['action'],entry['source'],entry['inputDigest'])
    return selected.submit()


def worker(args):
    layout=existing(args.state);job=Job(layout,args.operation)
    with writer(layout.private/'installer.lock'):
        request=job.bind_worker(args.attempt)
        initial=read_json(regular(layout.private/'initial.json'))
        config=Configuration(layout,layout.inspect(),initial['input'])
        if request['inputDigest']!=config.owner['inputDigest']:
            raise RuntimeError('Restore the original installation input')
        from .installation import Installation
        from .repair import worker_admission
        worker_admission(job,request)
        installer=Installation(job,config)
        try:return installer.run()
        except Exception as error:
            installer.saved(outcome='failed',errorType=type(error).__name__)
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True,metavar='{install,upgrade,repair-bootstrap,start,stop,status}')
    for name in ('install','upgrade'):
        item=commands.add_parser(name)
        item.add_argument('--config',type=Path,required=True)
        item.add_argument('--root',type=Path,default=Path('/opt/ioalab-runtime'))
        item.add_argument('--state',type=Path,default=Path('/var/lib/ioalab'))
        item.add_argument('--config-dir',type=Path,default=Path('/etc/ioalab'))
        item.add_argument('--core-user',default='ioalab-core')
        item.add_argument('--agents-user',default='ioalab-agents')
    item=commands.add_parser('repair-bootstrap')
    item.add_argument('--state',type=Path,required=True)
    item.add_argument('--operation',required=True)
    item=commands.add_parser('status');item.add_argument('--state',type=Path,default=Path('/var/lib/ioalab'))
    for name in ('start','stop'):
        item=commands.add_parser(name);item.add_argument('--state',type=Path,default=Path('/var/lib/ioalab'))
        if name=='stop':item.add_argument('--wait-seconds',type=float,default=30)
    item=commands.add_parser('_worker');item.add_argument('--state',type=Path,required=True)
    item.add_argument('--operation',required=True);item.add_argument('--attempt',type=int,required=True)
    args=parser.parse_args(argv)
    try:
        require_host()
        if args.command in ('start','stop'):
            from . import lifecycle
            layout=existing(args.state)
            answer=lifecycle.start(layout) if args.command=='start' else lifecycle.stop(layout,wait_seconds=args.wait_seconds)
        elif args.command=='repair-bootstrap':
            from .repair import submit as repair_submit
            answer=repair_submit(existing(args.state),Path(__file__).resolve().parent.parent,args.operation)
        else:
            answer=status(existing(args.state)) if args.command=='status' else worker(args) if args.command=='_worker' else submit(args)
        print(json.dumps(answer,ensure_ascii=False))
        return 0 if answer['status'] in ('completed','waiting') else 1
    except Exception as error:
        # No traceback or raw upstream response: initial input and child logs are private.
        print(json.dumps({'status':'not_ready','error':{'type':type(error).__name__,'message':str(error)}},ensure_ascii=False))
        return 1


if __name__=='__main__':raise SystemExit(main())
