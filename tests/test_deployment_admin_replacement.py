"""The explicit replacement resumes original paths, leaving the account's other state intact."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy import administrator
from deploy.files import save


def test_replacement_resumes_a_partial_archive_without_copying_old_identity(tmp_path,monkeypatch):
    state=tmp_path/'state';private=state/'.deployment';private.mkdir(parents=True)
    home=state/'agents/admin-assistant';swarm=state/'agents/swarm-admin'
    preparation=private/'admin-assistant'
    for directory in (home,swarm,preparation): directory.mkdir(parents=True)
    save(home/'config.json',{'username':'ISC_001'})
    save(preparation/'state.json',{'phase':'prepared','identity':{'agentId':'urn:old-managed-assistant'}})
    (home/'old-private-key').write_text('original')
    (swarm/'old-session').write_text('original')
    unrelated=state/'other-account';unrelated.mkdir();(unrelated/'balance').write_text('321.00')
    layout=SimpleNamespace(state=state,private=private,environments=tmp_path/'env',config=tmp_path/'config')
    calls=[];metadata={'paths':{str(home):{},str(swarm):{}},'administratorPrepared':True}
    config=SimpleNamespace(record={'files':{str(preparation/'config.json'):{'phase':'ready'}}},persist=lambda:None)
    def new_configuration(workspace):
        preparation.mkdir(exist_ok=True)
        save(preparation/'config.json',{'new':'admin'})
    config.administrator=new_configuration
    installer=SimpleNamespace(metadata=metadata,config=config,save_metadata=lambda:None,
        command=lambda *a,**k:calls.append(a[0]),workspace=lambda *a,**k:{'new':'workspace'},
        permissions=lambda:None,accounts=SimpleNamespace(reserve=lambda *a,**k:None))
    flow=SimpleNamespace(owner='deployment-owner',status=lambda:{'phase':'stopped'},
                         leases=lambda:nullcontext(),installer=lambda:installer)
    monkeypatch.setattr(administrator,'Lifecycle',lambda _:flow)
    rename=Path.rename
    def fail_after_first_move(source,target):
        if source==swarm:raise OSError('interrupted archive')
        return rename(source,target)
    monkeypatch.setattr(Path,'rename',fail_after_first_move)
    with pytest.raises(OSError):administrator.replace(layout)
    monkeypatch.setattr(Path,'rename',rename)
    assert administrator.replace(layout)['replacement']=='configured'
    assert (private/'retired-isc-assistant/runtime/old-private-key').read_text()=='original'
    assert (private/'retired-isc-assistant/swarm/old-session').read_text()=='original'
    assert not home.exists() and not swarm.exists()
    assert (unrelated/'balance').read_text()=='321.00'
    assert metadata['administratorAccount']=='admin' and metadata['administratorPrepared'] is False
    retire=[c for c in calls if 'retire-admin-assistant' in c]
    assert len(retire)==1
    assert administrator.replace(layout)['replacement']=='already_completed'
