"""Explicit, resumable replacement of the old ISC assistant by a fresh admin instance."""
from pathlib import Path

from .files import path_without_links, save, writer
from .release import read_json
from .lifecycle import Lifecycle
from .layout import read_environment


def replace(layout):
    flow = Lifecycle(layout)
    with writer(layout.private/'launcher.lock'), writer(layout.private/'installer.lock'):
        observed = flow.status()
        if observed['phase'] != 'stopped':
            raise RuntimeError('Normally stop all original components before replacing the administrator assistant')
        with flow.leases():
            pass
        installer = flow.installer()
        record_path = layout.private/'replace-admin-assistant.json'
        archive = layout.private/'retired-isc-assistant'
        if record_path.exists():
            record = read_json(record_path)
            if record['deploymentId'] != flow.owner:
                raise RuntimeError('Replacement belongs to another deployment')
            if record['phase'] == 'completed':
                return {'status':'completed','account':'admin','replacement':'already_completed'}
        else:
            preparation = read_json(layout.private/'admin-assistant/state.json')
            runtime = read_json(layout.state/'agents/admin-assistant/config.json')
            if preparation.get('phase') != 'prepared' or runtime.get('username') != 'ISC_001':
                raise RuntimeError('Only the originally prepared ISC_001 instance can be replaced')
            record = {'deploymentId':flow.owner,'phase':'planned','agentId':preparation['identity']['agentId']}
            save(record_path, record)
        core_python = layout.environments/'current/core/bin/python'
        platform = layout.config/'core/platform.json'
        installer.command([core_python,'-I','-m','ioalab','--config',platform,'upgrade-admin-email'],role='core')
        if record['phase'] == 'planned':
            installer.command([core_python,'-I','-m','ioalab','--config',platform,
                               'retire-admin-assistant','--agent',record['agentId']],role='core')
            record['phase'] = 'retired'; save(record_path, record)
        if record['phase'] == 'retired':
            archive.mkdir(mode=0o700, exist_ok=True)
            for source, name in ((layout.state/'agents/admin-assistant','runtime'),
                                 (layout.state/'agents/swarm-admin','swarm'),
                                 (layout.private/'admin-assistant','preparation')):
                source = path_without_links(source)
                target = path_without_links(archive/name)
                if not source.is_relative_to(layout.state) or target.parent != archive:
                    raise RuntimeError('Replacement path escaped the original deployment')
                if target.exists():
                    if source.exists(): raise RuntimeError('Both old and archived directories exist; inspect original replacement')
                else:
                    source.rename(target)
                installer.metadata['paths'].pop(str(source),None)
            installer.metadata['administratorPrepared'] = False
            installer.metadata['administratorAccount'] = 'admin'
            installer.save_metadata()
            recipe = layout.private/'admin-assistant/config.json'
            installer.config.record['files'].pop(str(recipe),None); installer.config.persist()
            record['phase'] = 'archived'; save(record_path, record)
        if record['phase'] == 'archived':
            workspace = installer.workspace('swarm-admin', defer_handoff=True)
            installer.accounts.reserve(layout.state/'agents/admin-assistant','agents',create=False)
            recipe = installer.config.administrator(workspace)
            installer.permissions()
            record['phase'] = 'configured'; save(record_path, record)
        record['phase'] = 'completed'; save(record_path, record)
        # Preparation needs the real core HTTP service, so normal start finishes it.
        return {'status':'completed','account':'admin','replacement':'configured','oldAgent':record['agentId']}


def prepare_for_start(installer):
    """After the core is ready, prepare the new fixed instance once; no model starts here."""
    if installer.metadata.get('administratorPrepared'):
        recipe = installer.layout.private/'admin-assistant/config.json'
        installer.installed('ioalab_admin_assistant','configure-email','--config',str(recipe),
                            '--recipient',installer.config.core()['runtime']['admin_email'])
        return
    recipe = installer.layout.private/'admin-assistant/config.json'
    installer.installed('ioalab_admin_assistant','prepare','--config',str(recipe),
                        environment=read_environment(installer.layout.config/'agents/assistant.env'))
    installer.accounts.handoff(installer.layout.state/'agents/admin-assistant','agents')
    installer.accounts.handoff(installer.layout.state/'agents/swarm-admin','agents')
    installer.metadata['administratorPrepared'] = True
    installer.save_metadata()


def mail_login(layout, sender):
    """Owner-driven device authorization, with credentials owned by the participant OS user."""
    import subprocess
    from .agently import ensure
    from .configuration import mailbox
    sender = mailbox(sender, 'sender')
    flow = Lifecycle(layout)
    installer = flow.installer()
    executable = ensure(layout)
    home = layout.state/'agents/agently-auth'
    installer.accounts.reserve(home,'agents')
    installer.accounts.handoff(home,'agents')
    identity = installer.role('agents')
    from .processes import child_environment
    environment = {**{k:v for k,v in child_environment().items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')},'HOME':identity['home'],
                   'AGENTLY_CLI_CONFIG_DIR':str(home),'AGENTLY_WORKSPACE':'ioalab-admin'}
    result = subprocess.run([str(executable),'auth','login'],env=environment,cwd=identity['home'],
                            user=identity['uid'],group=identity['gid'],extra_groups=[])
    if result.returncode:
        raise RuntimeError('Original Agently device authorization failed; inspect without automatic retry')
    check = subprocess.run([str(executable),'+me'],env=environment,cwd=identity['home'],capture_output=True,
                           text=True,user=identity['uid'],group=identity['gid'],extra_groups=[])
    import json
    value=json.loads(check.stdout)
    if check.returncode or not value.get('ok') or sender not in {a['email'] for a in value['data']['aliases']}:
        raise RuntimeError('Authorized mailbox differs from the selected sender')
    save(layout.private/'administrator-mail.json',{'transport':'agently','sender':sender})
    return {'status':'completed','sender':sender,'transport':'agently'}
