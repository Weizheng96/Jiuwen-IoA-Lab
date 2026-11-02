"""Switch a complete set, retain old business data, reject ambiguous crash recovery."""
from copy import deepcopy
import json
import os
from pathlib import Path
import uuid

import pytest

from deploy import activation
from deploy.layout import Layout
from deploy.release import UNITS, digest, read_json
from deploy.workspace import save
from test_production_release import release, source


@pytest.fixture
def owned(tmp_path):
    layout = Layout(tmp_path/'runtime', tmp_path/'state', tmp_path/'config')
    layout.claim()
    probe = tmp_path/'probe'
    try:
        probe.symlink_to(layout.root, target_is_directory=True)
    except OSError as exc:
        if os.name == 'nt' and exc.winerror == 1314:
            pytest.skip('Windows has no directory symlink privilege; Linux CI covers actual set switching')
        raise
    probe.unlink()
    return layout


def complete(layout, release_path, *, python='3.11.9'):
    document = read_json(release_path/'release.json')
    results = {}
    for unit in UNITS:
        identity = {'format':'ioalab.deployment-environment/1', 'layout':'compact-1', 'unit':unit,
                    'target':document['target'], 'python':{'version':python, 'implementation':'cpython-311'},
                    'plan':document['units'][unit]}
        environment_id = digest(identity)
        group = layout.environments/(unit+'-'+environment_id[:16])
        environment = group/('a-'+uuid.uuid4().hex[:16])
        environment.mkdir(parents=True)
        save(group/'owner.json', identity)
        save(group/'state.json', {'environmentId':environment_id,'phase':'ready','attempt':environment.name})
        save(environment/'deployment.json', {'environmentId':environment_id,'identity':identity,'versions':{}})
        if unit == 'participant':
            save(environment/'ioalab-release.json', {'format':'ioalab.access-installation/1','releaseId':environment_id,'versions':{}})
        (environment/'.ready').write_text(environment_id)
        results[unit] = {'status':'completed','unit':unit,'releaseId':document['releaseId'],
                         'environmentId':environment_id,'environment':str(environment)}
    return results


def candidate(layout, source, *, python='3.11.9', staging='one'):
    built = release(source, layout.root/staging)
    (layout.root/'releases').mkdir(exist_ok=True)
    destination = layout.root/'releases'/built.name
    if destination.exists():
        assert read_json(destination/'release.json') == read_json(built/'release.json')
    else:
        built.rename(destination)
    results = complete(layout, destination, python=python)
    return activation.prepare_set(layout, destination, results), results


def test_atomic_set_switch_retains_data_and_reuses_exact_candidate(owned, source, monkeypatch):
    first, _ = candidate(owned, source)
    sentinel = owned.state/'contract.sqlite'
    sentinel.write_bytes(b'original identity, contract and unknown payment receipt')
    assert activation.current(owned) is None
    assert activation.activate(owned, first, None)['changed'] is True
    second, _ = candidate(owned, source, python='3.11.10', staging='two')
    assert first['releaseId'] == second['releaseId'] and first['setId'] != second['setId']
    replace = activation.os.replace
    observations = []
    def observe(old, new):
        if Path(new).name == 'current':
            observations.append(activation.current(owned))
            replace(old, new)
            observations.append(activation.current(owned))
        else:
            replace(old, new)
    monkeypatch.setattr(activation.os, 'replace', observe)
    assert activation.activate(owned, second, first['setId'])['changed'] is True
    assert observations == [first, second]
    assert sentinel.read_bytes().startswith(b'original identity')
    assert (owned.environments/'sets'/first['setId']).is_dir()
    assert activation.activate(owned, second, first['setId'])['changed'] is False


def test_lost_activation_reply_recovers_same_set_without_second_replace(owned, source, monkeypatch):
    value, _ = candidate(owned, source)
    replace = activation.os.replace
    def lose_reply(old, new):
        replace(old, new)
        if Path(new).name == 'current': raise OSError('lost activation return')
    monkeypatch.setattr(activation.os, 'replace', lose_reply)
    with pytest.raises(OSError, match='lost activation'):
        activation.activate(owned, value, None)
    assert activation.current(owned) == value
    assert activation.activate(owned, value, None)['changed'] is False


def test_interrupted_replacement_keeps_whole_original_set(owned, source, monkeypatch):
    first, _ = candidate(owned, source)
    activation.activate(owned, first, None)
    second, _ = candidate(owned, source, python='3.11.10', staging='two')
    replace = activation.os.replace
    def interrupt(old, new):
        if Path(new).name == 'current': raise OSError('before current replace')
        replace(old, new)
    monkeypatch.setattr(activation.os, 'replace', interrupt)
    with pytest.raises(OSError): activation.activate(owned, second, first['setId'])
    assert activation.current(owned) == first


def test_different_current_is_not_overwritten_by_old_upgrade(owned, source):
    first, _ = candidate(owned, source)
    activation.activate(owned, first, None)
    second, _ = candidate(owned, source, python='3.11.10', staging='two')
    with pytest.raises(ValueError, match='saved upgrade'):
        activation.activate(owned, second, None)
    assert activation.current(owned) == first


@pytest.mark.parametrize('fault',['pending','wrong_owner','foreign_plan','wrong_profile_marker','changed_link','changed_release','missing_release','unknown_entry','set_json'])
def test_damaged_or_unfinished_set_never_replaces_current(owned, source, fault):
    first, _ = candidate(owned, source)
    activation.activate(owned, first, None)
    second, results = candidate(owned, source, python='3.11.10', staging='two')
    folder = owned.environments/'sets'/second['setId']
    environment = Path(results['participant']['environment'])
    if fault == 'pending':
        value = read_json(environment.parent/'state.json');value['phase']='installing';save(environment.parent/'state.json',value)
    elif fault == 'wrong_owner': save(environment.parent/'owner.json', {})
    elif fault == 'foreign_plan':
        value=read_json(environment/'deployment.json');value['identity']['plan']={};save(environment/'deployment.json',value)
    elif fault == 'wrong_profile_marker': save(environment/'ioalab-release.json', {})
    elif fault == 'changed_link':
        (folder/'core').unlink();(folder/'core').symlink_to(environment,target_is_directory=True)
    elif fault == 'changed_release':
        (folder/'release').unlink();(folder/'release').symlink_to(owned.state,target_is_directory=True)
    elif fault == 'missing_release': (folder/'release').unlink()
    elif fault == 'unknown_entry': (folder/'unrecorded').write_text('unexpected')
    else: save(folder/'set.json',{})
    with pytest.raises((ValueError, KeyError)):
        activation.activate(owned,second,first['setId'])
    assert activation.current(owned)==first


def test_arbitrary_release_and_external_environment_are_rejected(owned,source):
    built=release(source,owned.root/'build')
    results=complete(owned,built)
    with pytest.raises(ValueError,match='release root'): activation.prepare_set(owned,built,results)
    (owned.root/'releases').mkdir();destination=owned.root/'releases'/built.name;built.rename(destination)
    results['core']['environment']=str(owned.state)
    with pytest.raises(ValueError,match='escapes'): activation.prepare_set(owned,destination,results)
    assert activation.current(owned) is None


def test_nonlink_current_is_preserved(owned,source):
    value,_=candidate(owned,source)
    (owned.environments/'current').mkdir()
    original=owned.environments/'current'/'data';original.write_bytes(b'operator data')
    with pytest.raises(ValueError,match='managed'): activation.activate(owned,value,None)
    assert original.read_bytes()==b'operator data'


def test_release_resources_switch_with_the_complete_set_without_rewriting_configuration(owned,source):
    seed=source/'deploy/default-seed.json'
    seed.write_text('{"generation":"one"}',encoding='utf-8')
    first,_=candidate(owned,source)
    activation.activate(owned,first,None)
    manifest=read_json(Path(first['release'])/'release.json')
    relative=manifest['assets']['seed']['path']
    stable=owned.environments/'current/release'/relative
    config=owned.config/'core.env'
    config.write_text('IOALAB_SEED_FILE='+str(stable),encoding='utf-8')
    original=config.read_bytes()
    assert json.loads(stable.read_text(encoding='utf-8'))=={'generation':'one'}
    seed.write_text('{"generation":"two"}',encoding='utf-8')
    second,_=candidate(owned,source,python='3.11.10',staging='two')
    assert first['releaseId']!=second['releaseId']
    assert json.loads(stable.read_text(encoding='utf-8'))=={'generation':'one'}
    activation.activate(owned,second,first['setId'])
    assert json.loads(stable.read_text(encoding='utf-8'))=={'generation':'two'}
    assert config.read_bytes()==original
    assert json.loads((Path(first['release'])/relative).read_text(encoding='utf-8'))=={'generation':'one'}
