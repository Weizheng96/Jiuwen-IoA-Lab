"""Prepare only our native user application plugin; never alter the Swarm install."""
from importlib.resources import files
from pathlib import Path
import os
import re
import secrets
import tempfile

from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_text, restrict
from ioalab_sdk.runtime.protocol import RuntimeFault, encode
from .process import ProcessHost
from .web_notifications import WebNotifications


def prepare_extension(config_path, profile):
    config_path = Path(config_path).resolve()
    config = RuntimeConfig.load(config_path)
    if profile not in {item['key'] for item in config.identities}:
        raise RuntimeFault('invalid_input', 'Select an owned local identity profile')
    host_config = dict(config.host)
    specs = host_config.pop('business_tools', [])
    if not isinstance(specs, list): raise RuntimeFault('invalid_input', 'Configure local business factories')
    selected = [item for item in specs if isinstance(item, dict) and item.get('factory') == 'jiuwen-web' and item.get('profile') == profile]
    if len(selected) != 1:
        raise RuntimeFault('not_ready', 'Explicitly configure exactly one jiuwen-web factory for this profile')
    host = ProcessHost.from_config(host_config, base_dir=config_path.parent)
    data = host.data_dir
    if not data.is_dir() or not (data / 'config/config.yaml').is_file():
        raise RuntimeFault('not_ready', 'Select an existing Jiuwen workspace; no new host will be initialized')
    options = selected[0]['options']
    name = options.get('secret_env') if isinstance(options, dict) else None
    if not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', name):
        raise RuntimeFault('invalid_input', 'Configure a local notification credential name')
    credential = options.get('credentials_file')
    if os.environ.get(name) is None and isinstance(credential, str) and credential:
        credential = Path(credential).expanduser()
        if not credential.is_absolute(): credential = config_path.parent / credential
        credential = credential.resolve()
        if not credential.exists():
            if not credential.is_relative_to(config_path.parent):
                raise RuntimeFault('invalid_input', 'Create new notification credentials only inside the IoA configuration directory')
            credential.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    restrict(credential)
                    stream.write(name + '=' + secrets.token_urlsafe(32) + '\n')
            except FileExistsError: pass  # Another explicit preparation won; read its original value below.
    queue = WebNotifications(options, home=config.home, profile=profile, base_dir=config_path.parent)
    root = data / 'application_plugins'
    target = root / 'ioa-notifications'
    if not target.resolve().is_relative_to(data.resolve()):
        raise RuntimeFault('invalid_input', 'Use the native application directory inside the selected workspace')
    source = files('ioalab_jiuwen').joinpath('resources', 'ioa-notifications')
    content = {name: source.joinpath(name).read_text(encoding='utf-8') for name in ('extension.py', 'extension.yaml')}
    content['settings.local.json'] = encode({'user_id': queue.client.user, 'secret': queue.client.secret}) + '\n'
    if target.exists():
        if not target.is_dir() or any(path.name not in {*content, '__pycache__'} for path in target.iterdir()) or any(
            not (target / name).is_file() or (target / name).is_symlink() or (target / name).read_text(encoding='utf-8') != value
            for name, value in content.items()
        ):
            raise RuntimeFault('configuration_changed', 'Existing IoA notification files or target differ; review before an explicit migration', 'review_web_extension')
    else:
        root.mkdir(parents=True, exist_ok=True)
        # Discovery scans immediate children. The staging root has no manifest/entry;
        # the complete nested payload appears under its final name in one rename.
        with tempfile.TemporaryDirectory(prefix='.ioa-prepare-', dir=root) as temporary:
            stage = Path(temporary) / 'payload'
            stage.mkdir(mode=0o700)
            restrict(stage, directory=True)
            for name, value in content.items(): private_text(stage / name, value)
            try: stage.rename(target)
            except OSError:
                raise RuntimeFault('configuration_changed', 'The native plugin target changed while preparing it; inspect the original files', 'review_web_extension') from None
    return {'status': 'prepared', 'path': str(target), 'userId': queue.client.user, 'loaded': False,
            'next': 'If needed, restart Jiuwen through its original entry; runtime capabilities must confirm actual readiness'}
