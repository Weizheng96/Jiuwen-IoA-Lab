"""S02 per-launch criterion shared by the explicit SDK and native adapters.

Only static launch prerequisites are inspected: no Claude process, version
command, SDK handshake, inference, credential or settings content is read.
Authentication and model routing are recorded by actual S01 failures.
"""
import os
from pathlib import Path
import threading

CHECK_SECONDS=5


def unavailable(basis,**facts):
    return {'state':'unavailable','basis':basis,**facts}


def config_directory(environ):
    """The directory the launched CLI reads: CLAUDE_CONFIG_DIR, else ~/.claude."""
    value=environ.get('CLAUDE_CONFIG_DIR')
    return Path(value) if value else Path.home()/'.claude'


def _listable(path):
    with os.scandir(path) as entries:next(entries,None)


def launch_facts(*,cli,config,directories=(),files=()):
    """Four-state result from paths that must exist for one new launch."""
    exe=Path(cli)
    if not exe.is_file():return unavailable('Claude executable is missing')
    if os.name!='nt' and not os.access(exe,os.X_OK):return unavailable('Claude executable is not executable')
    for label,path in directories:
        path=Path(path)
        if not path.is_dir():return unavailable(label+' is missing')
        try:_listable(path)
        except PermissionError:return unavailable(label+' is unreadable')
    for label,path in files:
        if not Path(path).is_file():return unavailable(label+' is missing')
    config=Path(config)
    # An absent directory is created by the CLI on launch; it is not evidence
    # that launch fails. An existing unreadable one is.
    state='absent'
    if config.exists():
        if not config.is_dir():return unavailable('Claude configuration directory is not a directory')
        try:_listable(config)
        except PermissionError:return unavailable('Claude configuration directory is unreadable')
        state='readable'
    return {'state':'available','configDirectory':state,
            'basis':'Launch prerequisites present; no process, handshake or inference was run'}


def bounded(probe,timeout=None):
    """Run a static probe within its bound; an unfinished check is unknown."""
    box={}
    def run():
        try:box['value']=probe()
        except Exception as exc:
            box['value']={'state':'unknown','basis':'Launch self-check failed','errorType':type(exc).__name__}
    worker=threading.Thread(target=run,name='claude-s02-check',daemon=True)
    worker.start();worker.join(CHECK_SECONDS if timeout is None else timeout)
    if worker.is_alive() or 'value' not in box:
        return {'state':'unknown','basis':'Launch self-check did not finish within its bound'}
    return box['value']
