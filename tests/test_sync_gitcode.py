"""Real Git repositories exercise publication without contacting either hosting service."""
from pathlib import Path

import pytest

from tools import sync_gitcode
from tools.sync_gitcode import Git, SyncError, synchronize


def commit(git, message):
    git.call('add', 'README.md')
    git.call('commit', '-m', message)
    return git.text('rev-parse', 'HEAD')


@pytest.fixture
def repositories(tmp_path):
    root = Git(tmp_path)
    source, target, work = (tmp_path/name for name in ('source.git', 'target.git', 'work'))
    root.call('init', '--bare', str(source))
    root.call('init', '--bare', str(target))
    root.call('init', '-b', 'main', str(work))
    git = Git(work)
    git.call('config', 'user.name', 'Sync Test')
    git.call('config', 'user.email', 'sync@example.invalid')
    git.call('config', 'core.autocrlf', 'false')
    (work/'README.md').write_text('initial\n', encoding='utf-8')
    head = commit(git, 'initial')
    git.call('push', str(source), 'main')
    return git, str(source), str(target), head


def test_empty_dry_run_atomic_tags_idempotence_and_extra_refs_preserved(repositories):
    git, source, target, head = repositories
    git.call('tag', '-a', 'design-v0.0.1', '-m', 'annotated')
    git.call('tag', 'design-v0.0.2')
    git.call('tag', 'unrelated')
    git.call('checkout', '-b', 'side')
    (git.root/'README.md').write_text('side\n', encoding='utf-8')
    commit(git, 'side')
    git.call('tag', 'design-v9.0.0')
    git.call('checkout', 'main')
    git.call('push', source, '--tags')
    git.call('push', target, head+':refs/heads/keep', head+':refs/tags/keep')
    planned = synchronize(git, source, target, dry_run=True)
    assert set(planned['updates']) == {'refs/heads/main','refs/tags/design-v0.0.1','refs/tags/design-v0.0.2'}
    assert not git.text('ls-remote', '--heads', target, 'refs/heads/main')
    applied = synchronize(git, source, target)
    assert applied['status'] == 'synchronized'
    actual = git.text('ls-remote', target)
    assert 'refs/heads/keep' in actual and 'refs/tags/keep' in actual
    assert 'refs/tags/unrelated' not in actual
    assert 'refs/tags/design-v9.0.0' not in actual
    assert synchronize(git, source, target)['updates'] == {}
    (git.root/'README.md').write_text('next\n', encoding='utf-8')
    next_head = commit(git, 'next')
    git.call('push', source, 'main')
    assert synchronize(git, source, target)['updates'] == {'refs/heads/main':next_head}


@pytest.mark.parametrize('failure', ['dirty','unpublished','diverged','tag-conflict','historical-private'])
def test_rejects_unsafe_publication_without_changing_target(repositories, failure):
    git, source, target, initial = repositories
    if failure == 'dirty':
        (git.root/'README.md').write_text('unreviewed\n', encoding='utf-8')
    elif failure == 'unpublished':
        (git.root/'README.md').write_text('unpublished\n', encoding='utf-8');commit(git, 'unpublished')
    elif failure == 'diverged':
        git.call('checkout', '--orphan', 'other')
        (git.root/'README.md').write_text('unrelated\n', encoding='utf-8')
        other = commit(git, 'other')
        git.call('push', target, other+':refs/heads/main');git.call('checkout', 'main')
    elif failure == 'tag-conflict':
        git.call('tag', '-a', 'design-v0.0.1', '-m', 'source tag')
        git.call('push', source, 'refs/tags/design-v0.0.1')
        git.call('push', target, initial+':refs/tags/design-v0.0.1')
    else:
        private = git.root/'data';private.mkdir();(private/'fixture.txt').write_text('dummy fixture')
        git.call('add', 'data/fixture.txt');git.call('commit', '-m', 'private path')
        git.call('rm', 'data/fixture.txt');git.call('commit', '-m', 'removed but historical')
        git.call('push', source, 'main')
    before = git.text('ls-remote', target)
    with pytest.raises(SyncError):synchronize(git, source, target)
    assert git.text('ls-remote', target) == before


@pytest.mark.parametrize('moved', ['source-tag','existing-target-tag'])
def test_detects_tag_object_movement_even_when_main_and_peeled_commit_stay_same(repositories, monkeypatch, moved):
    git, source, target, head = repositories
    git.call('tag', '-a', 'design-v0.0.1', '-m', 'original')
    git.call('tag', '-a', 'other', '-m', 'different tag object for same commit')
    alternate = git.text('rev-parse', 'refs/tags/other')
    git.call('push', source, '--tags');git.call('push', target, '--tags')
    original = sync_gitcode.refs
    calls = {source:0,target:0}
    def observe(git, url, endpoint=None):
        calls[url] += 1
        if calls[url] == 2 and url == (source if moved == 'source-tag' else target):
            Git(Path(url)).call('update-ref', 'refs/tags/design-v0.0.1', alternate)
        return original(git, url, endpoint)
    monkeypatch.setattr(sync_gitcode, 'refs', observe)
    with pytest.raises(SyncError):synchronize(git, source, target)
    if moved == 'source-tag':
        assert not git.text('ls-remote', '--heads', target, 'refs/heads/main')


def test_gitcode_transport_keeps_tls_and_disables_trace_and_inherited_proxy(monkeypatch, tmp_path):
    from types import SimpleNamespace
    observed = {}
    def execute(argv, **kwargs):
        observed.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setenv('GIT_SSL_NO_VERIFY', '1')
    monkeypatch.setenv('GIT_TRACE_CURL', '1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://unrelated.invalid:1')
    monkeypatch.setattr(sync_gitcode.subprocess, 'run', execute)
    Git(tmp_path).call('ls-remote', sync_gitcode.GITCODE, endpoint='gitcode')
    assert 'GIT_SSL_NO_VERIFY' not in observed['env'] and 'GIT_TRACE_CURL' not in observed['env']
    assert 'http.'+sync_gitcode.GITCODE+'.proxy=' in observed['argv']
    assert 'http.'+sync_gitcode.GITCODE+'.sslVerify=true' in observed['argv']


@pytest.mark.parametrize('path', ['.agents/llm_apikey','.agents/data/demo/record.json','.agents/data/private-model.json',
                                  '.agents/newllmkey.txt','.agents/server_new.txt','.agents/server-connection.json'])
def test_history_guard_refuses_private_development_source_and_data(path):
    class History:
        def text(self, *args): return '0'*40+' '+path
    with pytest.raises(SyncError): sync_gitcode.check_history_paths(History(),'HEAD')

def test_history_guard_accepts_public_llm_tool_and_skill():
    class History:
        def text(self, *args): return '0'*40+' tools/llm.py\n'+'1'*40+' .agents/skills/llm/SKILL.md'
    sync_gitcode.check_history_paths(History(),'HEAD')
