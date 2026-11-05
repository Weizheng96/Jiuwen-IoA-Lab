"""Linux-root installed-package preset probe; no model, mail or system-service changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.layout import Layout
from deploy.processes import child_environment
from deploy.workspace import Workspace


def snapshot(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--swarm-python',type=Path,required=True)
    parser.add_argument('--platform',required=True)
    args=parser.parse_args()
    if sys.platform!='linux' or os.geteuid()!=0:
        parser.error('This isolated experiment requires Linux root; Windows cannot prove root preparation')
    output=args.output.absolute()
    if output.exists():parser.error('Use a new output; existing roots and evidence are never adopted')
    layout=Layout(output/'runtime',output/'state',output/'configuration')
    identity=layout.claim()
    workspace=Workspace(args.swarm_python,layout.state/'agents/swarm-admin',identity['deploymentId'])
    assert workspace.prepare(None)['status']=='not_ready'
    # The preparation command must not probe or run a model. The normal serve
    # command remains unready with this deliberately unconfigured test selection.
    recipe=layout.private/'admin-assistant/config.json'
    from ioalab_sdk.runtime.files import private_json,read_json
    private_json(recipe,{'deployment_id':identity['deploymentId'],'platform_url':args.platform,'password':'001',
        'host':{'command':[str(workspace.command)],'data_dir':str(workspace.home),
                'model':'unconfigured-prepare-only-test'},
        'web_url':'ws://127.0.0.1:18901/ws?user_id=ioa-admin','recipient':'owner@example.test'})
    environment=child_environment()
    def call(command,success=True):
        child=subprocess.run([sys.executable,'-I','-m','ioalab_admin_assistant',command,'--config',str(recipe)],
            cwd=output,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=60)
        if (child.returncode==0)!=success:
            raise RuntimeError('Installed administrator command did not return the expected bounded result')
        value=json.loads(child.stdout)
        assert value['status']==('completed' if success else 'rejected')
        return value
    first=call('prepare')
    home=layout.state/'agents/admin-assistant'
    state=read_json(recipe.with_name('state.json'))
    assert set(state['presets'])=={'message.forward','meeting.request'}
    assert all(p['result']['revision']==1 for p in state['presets'].values())
    assert first['meetingInitiallyEnabled'] is False and first['modelStarted'] is False
    original=snapshot(home)
    assert call('prepare')==first
    assert call('status')==first
    assert snapshot(home)==original
    assert state['webSecret'] not in json.dumps(first)
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_sdk.runtime.session import AccountSession
    config=RuntimeConfig.load(home/'config.json')
    account=AccountSession(config.platform,config.username,config.password,config.home)
    try:
        current=account.api('GET','/messaging/accounts/ISC_001/interfaces')
        assert [x['name'] for x in current]==['meeting.request','message.forward']
        assert [x['enabled'] for x in current]==[False,True]
    finally:account.close()
    from ioalab_sdk.runtime.lease import Lease
    with Lease(config.home/'client.lock',timeout=0):pass
    recipe.chmod(0o666)
    try:assert call('prepare',success=False)['error']['code']=='forbidden'
    finally:recipe.chmod(0o600)
    from ioalab_admin_assistant import preparation
    assert Path(preparation.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    print(json.dumps({'status':'passed','installedModule':True,'realLinuxRootOwnership':True,
        'realHTTPPublications':True,'fixedTwoPresets':True,'missingMailDisablesMeeting':True,
        'repeatAndStatusPreserveFiles':True,'unsafeRecipeModeRejected':True,
        'originalSDKLockReleased':True,'callsModel':False,'sendsMail':False,'runtimeReady':False}))


if __name__=='__main__':main()
