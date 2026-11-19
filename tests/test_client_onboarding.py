"""Public onboarding CLI, native installation and bound launcher behavior."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'src/client/skills'


@pytest.fixture
def client_runtime_installation(monkeypatch):
    from ioalab_sdk.runtime import installation
    version = installation.installed_version
    # The source test runtime has SDK wheel metadata but no built client wheel.
    monkeypatch.setattr(installation, 'installed_version',
                        lambda name: '0.0.186' if name == 'ioalab-client' else version(name))
    return installation.current('ioalab-client')


@pytest.mark.parametrize('existing_default', [False, True])
def test_skills_install_handoff_separates_actions_and_registration_preserves_default(tmp_path, monkeypatch, capsys, existing_default):
    from functools import partial
    from types import SimpleNamespace
    from ioalab_client import cli, native_skills
    from ioalab_client.runtime import merge_host
    from ioalab_sdk.runtime import RuntimeConfig, private_json
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    config = tmp_path/'client/settings.json'
    initial = {'username': 'clean-account', 'password': 'secret-not-for-handoff',
               'data_dir': str(tmp_path/'client/data'),
               'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'Assistant'}]}
    if existing_default:
        initial['host'] = {'default': 'original', 'registrations': {'original': {'type': 'claude_code'}}}
    private_json(config, initial)
    monkeypatch.setattr(cli, 'installation', SimpleNamespace(prepare=lambda: {
        'status': 'completed', 'data': {'python': sys.executable}}))
    # Source checkout tests provide the same real resources explicitly as the other install tests;
    # wheels receive these files at ioalab_client/data/skills during packaging.
    monkeypatch.setattr(native_skills, 'install', partial(native_skills.install, source=SOURCE))
    before = config.read_bytes()
    assert cli.main(['skills-install', '--host', 'codex', '--host-ref', 'current', '--config', str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    handoff = result['data']['onboarding']
    assert handoff['format'] == 'ioalab.onboarding/1'
    pending = {step['id']: step for step in handoff['pending']}
    assert pending['host']['actor'] == pending['native_setup']['actor'] == 'agent'
    assert pending['new_session']['actor'] == 'user'
    assert all(step['verify'][-2:] == ['--verify', step['id']] for step in pending.values())
    instruction = pending['host']['instruction']
    payload = json.JSONDecoder().raw_decode(instruction[instruction.index('{'):])[0]
    assert payload['expectedDigest'] == RuntimeConfig.load(config).public_summary()['configurationDigest']
    assert payload['changes'] == {'host': {'registrations': {'current': {'type': 'codex'}}}}
    merged = merge_host(initial.get('host', {}), payload['changes']['host'])
    assert merged['default'] == ('original' if existing_default else 'current')
    assert config.read_bytes() == before and not (tmp_path/'client/data').exists()
    assert 'secret-not-for-handoff' not in json.dumps(result)
    assert cli.main(['skills-install', '--host', 'codex', '--host-ref', 'current', '--config', str(config), '--format', 'markdown']) == 0
    rendered = capsys.readouterr().out
    assert '[智能体执行]' in rendered and '[由你完成]' in rendered
    assert 'runtime.configure' in rendered and 'runtime.entry.bind' in rendered


def test_fixed_handoff_before_account_setup_never_claims_complete(tmp_path, monkeypatch, capsys):
    from ioalab_client.cli import main
    from ioalab_client import installation
    monkeypatch.setattr(installation, 'prepare', lambda: {'status': 'completed', 'data': {'python': sys.executable}})
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert main(['onboarding', '--host', 'codex', '--config', str(tmp_path/'client/settings.local.json')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'waiting'
    data = result['data']
    assert data['format'] == 'ioalab.onboarding/1'
    assert data['website']['myAgents'] == 'https://ioalab.openjiuwen.com/workbench'
    assert data['configurationFile'] == str((tmp_path/'client/settings.local.json').resolve())
    assert len(data['skills']) == 2 and data['newSessionVerified'] is False
    assert 'password' not in json.dumps(data).lower()


def test_bound_role_can_request_handoff_through_its_public_operation(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from ioalab_client import commands
    from ioalab_sdk.runtime import private_json
    path = tmp_path/'client/settings.local.json'
    private_json(path, {'username': 'local-account', 'password': 'private-test-secret',
                        'data_dir': str(tmp_path/'data'),
                        'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'My assistant'}]})
    args = tmp_path/'handoff.json'; args.write_text('{"host":"codex"}')
    monkeypatch.setattr(commands, 'installation', lambda: SimpleNamespace(prepare=lambda: {
        'status': 'completed', 'data': {'python': sys.executable}}))
    assert commands.main(['--role', 'assistant', '--config', str(path), 'runtime.onboarding', '--args-file', str(args)]) == 0
    text = capsys.readouterr().out
    assert json.loads(text)['data']['format'] == 'ioalab.onboarding/1'
    assert 'private-test-secret' not in text and not (tmp_path/'data').exists()


@pytest.mark.parametrize('host,folder', [('codex', '.agents'), ('claude_code', '.claude')])
def test_both_native_skills_share_config_without_exposing_it(tmp_path, monkeypatch, host, folder):
    from ioalab_client.native_skills import install, status
    from ioalab_sdk.runtime import private_json
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    config = tmp_path/'client/settings.local.json'
    config.parent.mkdir(); config.write_text('not JSON: secret configuration must never be exported by installer')
    # Only the SDK parses configuration; an unreadable one fails before any native write.
    with pytest.raises(RuntimeError) as refused: install(host, config=config, source=SOURCE)
    assert 'secret configuration' not in str(refused.value)
    assert not (tmp_path/folder).exists()
    private_json(config, {'username': 'installer-account', 'password': 'private-installer-secret',
                          'data_dir': str(tmp_path/'client/data'),
                          'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'Assistant'}]})
    result = install(host, config=config, source=SOURCE)
    assert result['installed'] and not result['newSessionVerified']
    for item in result['skills']:
        path = tmp_path/folder/'skills'/item['name']
        assert item['path'] == str(path)
        launcher = (path/'scripts/ioalab.py').read_text(encoding='utf-8')
        assert repr(str(config.resolve())) in launcher
        installed = b''.join(file.read_bytes() for file in path.rglob('*') if file.is_file())
        assert b'private-installer-secret' not in installed and b'installer-account' not in installed
    assert all(item['installed'] for item in status(host, config=config))
    assert install(host, config=config, source=SOURCE)['installed']
    # Another host reuses exactly the same binding without changing the client.
    other = 'claude_code' if host == 'codex' else 'codex'
    assert install(other, config=config, source=SOURCE)['binding'] == result['binding']


def test_conflicting_role_preserves_user_files_and_prepares_other_role(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path/'.agents/skills/ioalab-delegate'; path.mkdir(parents=True)
    original = path/'SKILL.md'; original.write_bytes(b'user-owned')
    result = install('codex', source=SOURCE)
    assert not result['installed']
    assert result['skills'][0]['status'] == 'manual_required'
    assert result['skills'][0]['instructions']
    assert result['skills'][1]['installed'], result['skills'][1]
    assert original.read_bytes() == b'user-owned'


def test_native_permission_failure_returns_manual_guidance_for_both_roles(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == tmp_path/'.agents/skills': raise PermissionError('native directory is read-only')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    result = install('codex', source=SOURCE)
    assert all(not item['installed'] and item['instructions'] and item['package'] for item in result['skills'])


def test_manual_native_extraction_retains_program_binding_and_can_be_verified(tmp_path, monkeypatch):
    from ioalab_client import export_skills
    from ioalab_client.native_skills import status
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    files = export_skills(tmp_path/'exports', source=SOURCE)
    native = tmp_path/'.agents/skills'
    for path in files:
        with zipfile.ZipFile(path) as archive: archive.extractall(native)
    assert all(item['installed'] for item in status('codex'))
    (native/'ioalab-provider/SKILL.md').write_bytes(b'user-modified')
    assert not status('codex')[1]['installed']


def test_jiuwen_exports_both_skills_before_assigning_import(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    result = install('jiuwen', source=SOURCE)
    assert not result['installed']
    assert all(item['status'] == 'exported' and '完成原生入口绑定后' in item['instructions'][0]
               for item in result['skills'])
    assert not (tmp_path/'.jiuwenswarm').exists()


def test_jiuwen_account_controls_work_before_role_import_but_bind_requires_native(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from ioalab_client import commands

    config = tmp_path/'client/settings.local.json'
    invoked = []

    def invoke(python, arguments, **options):
        assert python == Path(sys.executable) and not options
        invoked.append(arguments)
        return SimpleNamespace(returncode=0, stdout='{"status":"waiting","data":{}}', stderr='')

    monkeypatch.setattr(commands, 'installation', lambda: SimpleNamespace(prepare=lambda: {
        'status': 'completed', 'data': {'python': sys.executable}}))
    monkeypatch.setattr(commands, 'invoke', invoke)
    control = ['--role', 'assistant', '--host-type', 'jiuwen', '--host-ref', 'desk', '--config', str(config)]
    assert commands.main([*control, 'account.setup']) == 0
    assert commands.main([*control, 'account.setup.status']) == 0
    assert commands.main([*control, 'runtime.entry.bind']) == 1
    assert [json.loads(line)['status'] for line in capsys.readouterr().out.splitlines()] == ['waiting', 'waiting', 'rejected']
    assert invoked == [['-m', 'ioalab_sdk.access', 'account-setup', '--config', str(config), '--role', 'assistant', '--both-roles'],
                       ['-m', 'ioalab_sdk.access', 'account-setup-status', '--config', str(config)]]
    assert not config.exists()


@pytest.mark.parametrize('lost_import_receipt', [False, True])
def test_jiuwen_install_uses_one_native_extension_for_both_roles(tmp_path, monkeypatch, lost_import_receipt, client_runtime_installation):
    from http import HTTPStatus
    from shutil import copytree
    from threading import Thread
    from websockets.sync.server import serve
    from ioalab_client.native_skills import install
    from ioalab_sdk.runtime import LocalClient, RuntimeConfig

    config = tmp_path/'client/settings.local.json'
    workspace = tmp_path/'jiuwen'
    extensions = workspace/'agent/workspace/extensions'
    extensions.mkdir(parents=True)
    requests = []
    state = {}
    deny_handshake = False
    started = False
    connected = False

    def runtime_status(client, operation):
        assert operation == 'runtime.status'
        if not started:
            return {'status': 'waiting'}
        configured = RuntimeConfig.load(config)
        return {'status': 'completed', 'data': {'account': {'username': configured.username, 'platform': configured.platform,
                                                            'authenticated': connected}, 'platformConnected': connected,
                'installation': client_runtime_installation, 'configurationDigest': configured.public_summary()['configurationDigest']}}

    monkeypatch.setattr(LocalClient, 'call', runtime_status)

    def authorize(socket, request):
        if deny_handshake:
            return socket.respond(HTTPStatus.FORBIDDEN, 'Host permission required')

    def handle(socket):
        for frame in socket:
            request = json.loads(frame)
            method, params = request['method'], request['params']
            requests.append(method)
            if method == 'extensions.list':
                payload = {'extensions': list(state.values())}
            elif method == 'extensions.import':
                source = Path(params['folder_path'])
                binding = json.loads((source/'entry-binding.json').read_bytes())
                assert set(binding['profiles']) == {'assistant', 'provider'}
                assert binding['hostRef'] == 'current-jiuwen'
                assert 'fixture-secret' not in (source/'entry-binding.json').read_text()
                for role, skill in [('assistant', 'ioalab-delegate'), ('provider', 'ioalab-provider')]:
                    with zipfile.ZipFile(config.parent/'role-skills/current-jiuwen'/f'ioalab-{role}-skill.zip') as bundle:
                        assert all(file.read_bytes() == bundle.read(f'{skill}/native/{file.name}') for file in source.iterdir())
                copytree(source, extensions/source.name)
                payload = state[source.name] = {'name': source.name, 'class_name': 'IoAEntryRail', 'enabled': False}
                if lost_import_receipt:
                    return
            else:
                assert method == 'extensions.toggle' and params['enabled'] is True
                payload = state[params['name']]
                payload['enabled'] = True
            socket.send(json.dumps({'type': 'res', 'id': request['id'], 'ok': True, 'payload': payload}))

    with serve(handle, '127.0.0.1', 0, process_request=authorize) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            monkeypatch.setenv('JIUWENSWARM_START_CMD', '["jiuwenswarm"]')
            monkeypatch.setenv('JIUWENSWARM_DATA_DIR', str(workspace))
            monkeypatch.setenv('WEB_HOST', '127.0.0.1')
            monkeypatch.setenv('WEB_PORT', str(server.socket.getsockname()[1]))
            monkeypatch.setenv('WEB_PATH', '/ws')
            options = dict(config=config, source=SOURCE, host_ref='current-jiuwen')
            install('jiuwen', **options)
            assert requests == []
            config.write_text(json.dumps({'username': 'installer', 'password': 'fixture-secret', 'data_dir': 'data',
                'host': {'default': 'current-jiuwen', 'registrations': {'current-jiuwen': {'type': 'jiuwen'}}},
                'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'Assistant'},
                               {'key': 'seller', 'role': 'service', 'name': 'Provider'}]}))
            assert install('jiuwen', **options)['nativeExtension']['reason'] == 'runtime_pending'
            assert requests == []
            started = True
            assert install('jiuwen', **options)['nativeExtension']['reason'] == 'runtime_pending'
            assert requests == []
            connected = True
            first = install('jiuwen', **options)['nativeExtension']
            if lost_import_receipt:
                assert first['status'] == 'waiting' and first['reason'] == 'import_unconfirmed'
                assert requests == ['extensions.list', 'extensions.import']
                assert all(not item['enabled'] for item in state.values())
                first = install('jiuwen', **options)['nativeExtension']
                assert requests == ['extensions.list', 'extensions.import', 'extensions.list', 'extensions.toggle']
            else:
                assert requests == ['extensions.list', 'extensions.import', 'extensions.toggle'], first
            assert first['status'] == 'enabled' and first['nativeLoadedVerified'] is False, first
            completed = list(requests)
            assert install('jiuwen', **options)['nativeExtension']['status'] == 'enabled'
            assert requests == completed+['extensions.list']
            # A prior manually named import can be reused when its actual files match.
            (extensions/first['name']).rename(extensions/'manual_ioa')
            state['manual_ioa'] = state.pop(first['name'])
            state['manual_ioa']['name'] = 'manual_ioa'
            (extensions/'manual_ioa'/'README.md').write_bytes(b'old documentation')
            assert install('jiuwen', **options)['nativeExtension']['name'] == 'manual_ioa'
            assert requests == completed+['extensions.list']*2
            (extensions/'manual_ioa'/'worker.py').write_bytes(b'user edit')
            assert install('jiuwen', **options)['nativeExtension']['status'] == 'conflict'
            assert requests == completed+['extensions.list']*3
            assert (extensions/'manual_ioa'/'worker.py').read_bytes() == b'user edit'
            deny_handshake = True
            assert install('jiuwen', **options)['nativeExtension']['reason'] == 'permission_required'
            assert requests == completed+['extensions.list']*3
        finally:
            server.shutdown()
            thread.join()


def test_exported_launcher_refuses_account_rebinding_in_fresh_process(tmp_path):
    from ioalab_client import export_skills
    files = export_skills(tmp_path/'exports', source=SOURCE, config=tmp_path/'original.json')
    with zipfile.ZipFile(files[0]) as archive: archive.extractall(tmp_path/'skill')
    script = tmp_path/'skill/ioalab-delegate/scripts/ioalab.py'
    result = subprocess.run([sys.executable, str(script), 'runtime.status', '--config', str(tmp_path/'other.json')],
                            capture_output=True, encoding='utf-8')
    assert result.returncode == 1
    assert json.loads(result.stdout)['error']['code'] == 'identity_conflict'


def test_new_account_prepares_both_roles_but_existing_configuration_is_untouched(platform, tmp_path):
    from ioalab_sdk.runtime.account_setup import AccountSetupUi, setup_document
    from ioalab_sdk.runtime import RuntimeConfig
    from ioalab_sdk.provider import validate_provider
    path = tmp_path/'client/settings.local.json'
    options = {'platform_url': platform.s.base_url, 'prepare_both_roles': True}
    ui = AccountSetupUi(path, options)
    assert ui.decision({'mode': 'register'}, None)['authenticated']
    config = RuntimeConfig.load(path)
    assert {p['role'] for p in config.identities} == {'assistant', 'service'}
    assert validate_provider(config.provider, config.identities)['service']['accepting'] is False
    before = path.read_bytes()
    setup_document(path, options)
    assert path.read_bytes() == before
    # A user-only view can show the same credentials for website login; public receipts cannot.
    private = ui.snapshot()
    assert private['credentials']['username'] == config.username
    assert private['credentials']['password'] == config.password
    assert private['configurationFile'] == str(path.resolve())
    assert config.password not in json.dumps(json.loads(ui.receipt.read_text(encoding='utf-8')))


def test_handoff_uses_actual_platform_and_never_returns_configured_secrets(platform, tmp_path, monkeypatch):
    from ioalab_client.onboarding import summary, markdown
    from ioalab_sdk.runtime.account_setup import AccountSetupUi
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path/'client/settings.local.json'
    ui = AccountSetupUi(path, {'platform_url': platform.s.base_url, 'prepare_both_roles': True})
    ui.decision({'mode': 'register'}, None)
    secret = ui.snapshot()['credentials']['password']
    result = summary('codex', config=path)
    assert result['data']['website']['home'] == platform.s.base_url
    assert result['data']['accountName'] == ui.snapshot()['credentials']['username']
    assert secret not in json.dumps(result) and secret not in markdown(result)
    assert result['data']['newSessionVerified'] is False and result['status'] == 'waiting'


@pytest.mark.parametrize('mode', ['register', 'existing'])
def test_account_page_credentials_also_login_to_website(platform, client, tmp_path, mode):
    from ioalab_sdk.runtime.account_setup import AccountSetupUi
    from _support import new_user, PASSWORD
    ui = AccountSetupUi(tmp_path/'client/settings.local.json', {'platform_url': platform.s.base_url, 'prepare_both_roles': True})
    values = {'mode': mode}
    if mode == 'existing':
        user = new_user(platform)
        values.update(username=user['username'], password=PASSWORD)
    ui.decision(values, None)
    credentials = ui.snapshot()['credentials']
    response = client.post('/api/auth/login', json=credentials)
    assert response.status_code == 200
    assert client.get('/api/me').json()['username'] == credentials['username']
    assert client.get('/workbench').status_code == 200


def test_external_account_config_does_not_change_reported_client_location(tmp_path):
    from ioalab_client.layout import locations
    root = tmp_path/'client'; root.mkdir()
    (root/'ioalab-bootstrap.json').write_text('{}')
    python = root/'env'/'Scripts'/'python.exe'
    result = locations(tmp_path/'private/settings.json', python)
    assert result['clientDirectory'] == str(root)
    assert result['programDirectory'] == str(root/'env')
    assert result['configurationFile'] == str((tmp_path/'private/settings.json').resolve())


def test_handoff_separates_calling_registration_default_and_secret_options(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    from ioalab_client.onboarding import summary
    from ioalab_sdk.runtime import private_json
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    config=tmp_path/'client/settings.local.json'
    private_json(config, {'username':'same-account','password':'private-handoff-secret',
                         'data_dir':str(tmp_path/'data'),
                         'identities':[{'key':'personal','role':'assistant','name':'Assistant'}],
                         'host':{'default':'primary','registrations':{
                             'primary':{'type':'codex','command':['codex'],'model':'private-model-option'},
                             'secondary':{'type':'claude_code','command':['claude']}}}})
    assert install('claude_code',host_ref='secondary',config=config,source=SOURCE)['installed']
    result=summary('claude_code',host_ref='secondary',config=config)
    data=result['data']
    assert data['callingHostRef']=='secondary'
    assert (data['executionHost'],data['executionHostRef'])==('codex','primary')
    assert all(item['installed'] and item['entry'].startswith('/') for item in data['skills'])
    assert not data['authenticated'] and not data['platformConnected'] and not data['connected']
    assert 'private-handoff-secret' not in json.dumps(result)
    assert 'private-model-option' not in json.dumps(result)


def test_same_role_markdown_and_references_are_identical_across_three_host_exports(tmp_path):
    from ioalab_client.skills import export_skills
    snapshots=[]
    for host in ('codex','claude_code','jiuwen'):
        files=export_skills(tmp_path/host,source=SOURCE,host=host,host_ref='registered-'+host)
        roles=[]
        for path in files:
            with zipfile.ZipFile(path) as archive:
                names=archive.namelist()
                # native/ is the selected adapter's entry, binding and enablement text; host adapter tests own its content.
                assert any(n.endswith('/native/entry-binding.json') for n in names)
                assert any(n.endswith('/scripts/uninstall.ps1') for n in names)==(path.name=='ioalab-assistant-skill.zip')
                public={n:archive.read(n) for n in names if n.endswith('.md') and not n.split('/',1)[1].startswith('native/')}
                assert any(n.endswith('/SKILL.md') for n in public) and any('/references/' in n for n in public)
                roles.append(public)
                launcher=next(archive.read(n).decode() for n in names if n.endswith('/scripts/ioalab.py'))
                assert 'registered-'+host in launcher
        snapshots.append(roles)
    assert snapshots[0]==snapshots[1]==snapshots[2]


@pytest.mark.parametrize('package_host,package_ref',[('claude_code','codex'),('codex','other-codex')])
def test_native_status_refuses_other_host_or_registration_binding(tmp_path,monkeypatch,package_host,package_ref):
    from ioalab_client.skills import export_skills
    from ioalab_client.native_skills import status
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    for path in export_skills(tmp_path/'exports',source=SOURCE,host=package_host,host_ref=package_ref):
        with zipfile.ZipFile(path) as archive:archive.extractall(tmp_path/'.agents/skills')
    assert not any(item['installed'] for item in status('codex',host_ref='codex'))


def test_native_entry_binding_uses_sdk_profiles_and_fixed_launcher_commands(tmp_path):
    from ioalab_client.skills import native_entry_binding
    summary={'dataDir':str(tmp_path/'original-data'),'identities':[
        {'key':'actual-assistant-key','role':'assistant'}, {'key':'actual-service-key','role':'service'}]}
    result=native_entry_binding(python=sys.executable,config=tmp_path/'original.json',host_ref='original-host',summary=summary)
    assert result['profiles']['assistant']['profile']=='actual-assistant-key'
    assert result['profiles']['provider']['profile']=='actual-service-key'
    assert 'assistant.delegation.flow' in result['sourceOperations']
    assert 'runtime.stop' not in result['sourceOperations']
    assert 'runtime.configure' not in result['sourceOperations']
    assert 'assistant.unknown' not in result['sourceOperations']
    for slot,role in [('assistant','assistant'),('provider','service')]:
        item=result['profiles'][slot]
        assert item['ensureCommand']==[sys.executable,'-I','-X','utf8','-m','ioalab_client.runtime','start','--config',str((tmp_path/'original.json').resolve())]
        assert item['callerCommand'][-1]=='--native-input'
        assert item['callerCommand'][item['callerCommand'].index('--role')+1]==role
        assert 'entryRef' not in item and 'grant' not in item
        assert Path(item['descriptorFile']).is_relative_to(tmp_path/'original-data')


def test_complete_native_resources_are_exported_without_automatic_install(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import ioalab_claude_code
    from ioalab_client.skills import export_skills
    seen=[]
    def resource_files(binding):
        seen.append(binding)
        return {'entry-binding.json':json.dumps(binding).encode(),'wrapper.mjs':b'fixture-native-wrapper'}
    monkeypatch.setattr(ioalab_claude_code,'native_skills',SimpleNamespace(resource_files=resource_files))
    packages=export_skills(tmp_path/'exports',source=SOURCE,host='claude_code',host_ref='original-host',
                           public_summary={'dataDir':str(tmp_path/'data'),'identities':[{'key':'actual-key','role':'assistant'}]})
    assert len(seen)==1 and len(packages)==2
    for package in packages:
        with zipfile.ZipFile(package) as archive:
            root=archive.namelist()[0].split('/')[0]
            assert archive.read(root+'/native/wrapper.mjs')==b'fixture-native-wrapper'
            binding=json.loads(archive.read(root+'/native/entry-binding.json'))
            assert binding['profiles']['assistant']['profile']=='actual-key'
            manifest=json.loads(archive.read(root+'/.ioalab-skill.json'))
            assert 'native/wrapper.mjs' in manifest['files']


def test_native_export_metadata_is_sanitized_by_the_sdk(tmp_path):
    from ioalab_client.skills import entry_summary,native_entry_binding
    from ioalab_sdk.runtime import private_json
    config=tmp_path/'settings.json'
    private_json(config,{'username':'account-owner','password':'private-not-exported',
        'data_dir':str(tmp_path/'data'),'identities':[{'key':'original-profile','role':'assistant','name':'Original'}]})
    summary=entry_summary(config)
    binding=native_entry_binding(python=sys.executable,config=config,host_ref='original-host',summary=summary)
    assert binding['profiles']['assistant']['profile']=='original-profile'
    assert 'private-not-exported' not in json.dumps(binding)
    assert 'account-owner' not in json.dumps(binding)


def test_invalid_native_export_configuration_reports_no_input_content(tmp_path):
    from ioalab_client.skills import entry_summary
    from ioalab_sdk.runtime import private_json
    config=tmp_path/'settings.json'
    private_json(config,{'unexpected':'private-not-exported'})
    with pytest.raises(RuntimeError) as error:entry_summary(config)
    assert 'private-not-exported' not in str(error.value)
    assert 'SDK native entry metadata is unavailable' in str(error.value)


def test_adapter_refusing_entry_binding_returns_structured_export_failure(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    import ioalab_claude_code
    from ioalab_client.cli import main
    from ioalab_client import installation
    from ioalab_sdk.runtime import RuntimeFault
    def refuse(binding):raise RuntimeFault('invalid_input','Adapter refused the entry binding')
    monkeypatch.setattr(installation,'prepare',lambda:{'status':'completed','data':{'python':sys.executable}})
    monkeypatch.setattr(ioalab_claude_code,'native_skills',SimpleNamespace(resource_files=refuse))
    assert main(['skills','--output',str(tmp_path/'exports'),'--host','claude_code','--config',str(tmp_path/'missing.json')])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='rejected' and result['error']['code']=='export_failed'
    assert not any((tmp_path/'exports').glob('*.zip'))


def _account(config,data):
    from ioalab_sdk.runtime import private_json
    private_json(config,{'username':'reinstall-account','password':'private-reinstall-secret','data_dir':str(data),
        'identities':[{'key':'own-assistant','role':'assistant','name':'A'},{'key':'own-service','role':'service','name':'S'}]})


def test_reinstall_after_account_atomically_replaces_own_unmodified_skills(tmp_path,monkeypatch):
    from ioalab_client.native_skills import install,status
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    config=tmp_path/'client/settings.local.json'
    before=install('codex',config=config,source=SOURCE,host_ref='desk')
    assert before['installed'] and not before['reloadRequired']
    native=tmp_path/'.agents/skills'
    assert json.loads((native/'ioalab-delegate/native/entry-binding.json').read_bytes())['profiles']=={}
    _account(config,tmp_path/'client/data')
    after=install('codex',config=config,source=SOURCE,host_ref='desk')
    assert after['installed'] and after['reloadRequired'] and after['exportsReplaced']
    assert all(item['replaced'] and item['reloadRequired'] for item in after['skills'])
    profiles=json.loads((native/'ioalab-provider/native/entry-binding.json').read_bytes())['profiles']
    assert profiles['provider']['profile']=='own-service' and profiles['assistant']['profile']=='own-assistant'
    assert all(item['installed'] for item in status('codex',config=config,host_ref='desk'))
    # Neither a retired copy nor staging remains discoverable beside the two skills.
    assert sorted(path.name for path in native.iterdir())==['ioalab-delegate','ioalab-provider']
    assert sorted(path.name for path in (config.parent/'role-skills/desk').iterdir())==['ioalab-assistant-skill.zip','ioalab-provider-skill.zip']
    installed=b''.join(file.read_bytes() for file in native.rglob('*') if file.is_file())
    assert b'private-reinstall-secret' not in installed
    again=install('codex',config=config,source=SOURCE,host_ref='desk')
    assert again['installed'] and not again['reloadRequired'] and not again['exportsReplaced']


@pytest.mark.parametrize('change',['modified','other-ref','other-config','foreign-export'])
def test_reinstall_never_replaces_modified_or_differently_bound_skills(tmp_path,monkeypatch,change):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    config=tmp_path/'client/settings.local.json'
    first=tmp_path/'elsewhere/settings.local.json' if change=='other-config' else config
    install('codex',config=first,source=SOURCE,host_ref='other' if change=='other-ref' else 'desk',output=tmp_path/'first-exports' if change in ('other-ref','other-config') else None)
    native=tmp_path/'.agents/skills/ioalab-delegate'
    exports=config.parent/'role-skills/desk'
    if change=='modified':(native/'SKILL.md').write_bytes(b'user-edited skill')
    if change=='foreign-export':(exports/'ioalab-assistant-skill.zip').write_bytes(b'user file')
    snapshot={path:path.read_bytes() for path in native.rglob('*') if path.is_file()}
    _account(config,tmp_path/'client/data')
    if change=='foreign-export':
        with pytest.raises(RuntimeError,match='export differs'):install('codex',config=config,source=SOURCE,host_ref='desk')
        assert (exports/'ioalab-assistant-skill.zip').read_bytes()==b'user file'
    else:
        result=install('codex',config=config,source=SOURCE,host_ref='desk')
        delegate=result['skills'][0]
        assert delegate['status']=='manual_required' and not delegate['replaced'] and delegate['instructions']
        assert 'differs' in delegate['reason']
    assert {path:path.read_bytes() for path in native.rglob('*') if path.is_file()}==snapshot


@pytest.mark.parametrize('rollback_fails',[False,True])
def test_failed_swap_restores_or_preserves_original_skill_directory(tmp_path,monkeypatch,rollback_fails):
    from ioalab_client.native_skills import install,status
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    config=tmp_path/'client/settings.local.json'
    install('codex',config=config,source=SOURCE)
    native=tmp_path/'.agents/skills'
    snapshot={path.relative_to(native/path.relative_to(native).parts[0]):path.read_bytes()
              for path in native.rglob('*') if path.is_file() and path.relative_to(native).parts[0]=='ioalab-delegate'}
    _account(config,tmp_path/'client/data')
    original=Path.rename
    def rename(self,target):
        # Interrupt the swap of a staged directory into its final native place (and optionally its rollback).
        if Path(target).parent==native and self.parent.parent==native and (rollback_fails or self.name!='retired'):
            raise PermissionError('swap interrupted')
        return original(self,target)
    monkeypatch.setattr(Path,'rename',rename)
    result=install('codex',config=config,source=SOURCE)
    assert not result['installed'] and all(item['status']=='manual_required' for item in result['skills'])
    monkeypatch.setattr(Path,'rename',original)
    if rollback_fails:
        # Nothing is deleted: the reason names the nested, undiscoverable preserved copy.
        kept=Path(result['skills'][0]['reason'].rsplit(' at ',1)[1])
        assert kept.parent.parent==native and kept.name=='retired' and not (native/'ioalab-delegate').exists()
        assert {path.relative_to(kept):path.read_bytes() for path in kept.rglob('*') if path.is_file()}==snapshot
        return
    assert {path.relative_to(native/'ioalab-delegate'):path.read_bytes()
            for path in (native/'ioalab-delegate').rglob('*') if path.is_file()}==snapshot
    assert sorted(path.name for path in native.iterdir())==['ioalab-delegate','ioalab-provider']
    # The restored own copy remains replaceable once the interruption is gone.
    assert install('codex',config=config,source=SOURCE)['installed']
    assert all(item['installed'] for item in status('codex',config=config))


def test_pending_steps_are_structured_and_verified_read_only(tmp_path,monkeypatch,capsys,client_runtime_installation):
    from ioalab_client.cli import main
    from ioalab_client import installation
    from ioalab_sdk.runtime import LocalClient, RuntimeConfig, private_json, read_configuration
    monkeypatch.setattr(installation,'prepare',lambda:{'status':'completed','data':{'python':sys.executable}})
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    config=tmp_path/'client/settings.local.json'
    base=['onboarding','--host','jiuwen','--host-ref','desk','--config',str(config)]
    assert main(base)==0
    steps=json.loads(capsys.readouterr().out)['data']['pending']
    assert all(set(item)=={'id','actor','instruction','verify'} and item['actor'] in ('agent','user') for item in steps)
    account=next(item for item in steps if item['id']=='account')
    assert account['actor']=='user' and 'account.setup' in account['instruction']
    assert account['verify'][-2:]==['--verify','account'] and account['verify'][account['verify'].index('--config')+1]==str(config.resolve())
    assert all(item['id'] not in ('new_session', 'skill:ioalab-delegate', 'skill:ioalab-provider', 'execution_host') for item in steps)
    assert not any('在线试用' in item['instruction'] or '管理员助手' in item['instruction'] for item in steps)
    assert main([*base,'--verify','account'])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='step_pending'
    _account(config,tmp_path/'client/data')
    assert main([*base,'--verify','account'])==0
    assert json.loads(capsys.readouterr().out)['data']['verified'] is True
    assert main([*base,'--verify','skill:ioalab-delegate'])==1
    assert json.loads(capsys.readouterr().out)['data']['verified'] is False
    assert main([*base,'--verify','new_session'])==1
    assert 'password' not in capsys.readouterr().out.lower()

    # Only the registered current host's bound projection releases the user handoff.
    document = read_configuration(config)
    document['host'] = {'default': 'desk', 'registrations': {'desk': {'type': 'jiuwen'}}}
    private_json(config, document)
    configured = RuntimeConfig.load(config)
    binding = {'hostRef': 'desk', 'state': 'waiting', 'phase': 'reserved', 'operationId': 'original-setup'}
    runtime = {'status': 'completed', 'data': {'account': {'username': configured.username, 'platform': configured.platform},
        'installation': client_runtime_installation, 'configurationDigest': configured.public_summary()['configurationDigest'],
        'nativeEntryBindings': {'desk': binding, 'different': {'hostRef': 'different', 'state': 'completed', 'phase': 'bound'}}}}
    monkeypatch.setattr(LocalClient, 'call', lambda self, operation: runtime)
    assert main(base)==0
    steps=json.loads(capsys.readouterr().out)['data']['pending']
    setup=next(item for item in steps if item['id']=='native_setup')
    assert setup['actor']=='agent' and 'original-setup' in setup['instruction']
    assert all(item['actor']=='agent' and item['id'] != 'execution_host' for item in steps)

    binding.update(state='rejected', phase='migration_required')
    assert main(base)==0
    steps=json.loads(capsys.readouterr().out)['data']['pending']
    setup=next(item for item in steps if item['id']=='native_setup')
    assert '迁移原安装' in setup['instruction'] and 'original-setup' in setup['instruction']
    assert all(item['actor']=='agent' for item in steps)

    binding.update(state='completed', phase='bound', bindingRef='private-binding-reference')
    assert main(base)==0
    payload=json.loads(capsys.readouterr().out)['data']
    steps=payload['pending']
    user_steps=[item for item in steps if item['actor']=='user']
    assert [item['id'] for item in user_steps]==['skill:ioalab-delegate','skill:ioalab-provider','new_session']
    assert str(config.parent/'role-skills/desk/ioalab-assistant-skill.zip') in user_steps[0]['instruction']
    assert 'native_setup' not in [item['id'] for item in steps]
    assert 'execution_host' in [item['id'] for item in steps]
    assert 'private-binding-reference' not in json.dumps(payload)
    assert main([*base,'--verify','native_setup'])==0
    assert json.loads(capsys.readouterr().out)['data']['verified'] is True
    assert main([*base,'--verify','new_session'])==1
    assert json.loads(capsys.readouterr().out)['data']['verified'] is False
    assert main([*base,'--format','markdown'])==0
    text=capsys.readouterr().out
    assert '[由你完成]' in text and 'private-reinstall-secret' not in text
