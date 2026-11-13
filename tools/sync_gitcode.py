"""Publish reviewed GitHub main and reachable design tags to the GitCode main repository."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

GITHUB = 'https://github.com/Weizheng96/Jiuwen-IoA-Lab.git'
GITCODE = 'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git'


class SyncError(RuntimeError):
    pass


class Git:
    def __init__(self, root, github_proxy=None):
        self.root = Path(root)
        self.github_proxy = github_proxy

    def call(self, *args, endpoint=None, check=True):
        # Never inherit transport diagnostics that can print authentication headers.
        env = {k:v for k,v in os.environ.items()
               if not k.startswith('GIT_TRACE') and k not in ('GIT_CURL_VERBOSE', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_SSL_NO_VERIFY')}
        env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GIT_ASKPASS='', SSH_ASKPASS='')
        options = ['-c', 'core.quotePath=false']
        if endpoint == 'gitcode':
            options += ['-c', 'http.proxy=', '-c', 'http.https://gitcode.com/.proxy=',
                        '-c', 'http.sslVerify=true', '-c', 'http.'+GITCODE+'.proxy=',
                        '-c', 'http.'+GITCODE+'.sslVerify=true']
        elif endpoint == 'github':
            options += ['-c', 'http.sslVerify=true', '-c', 'http.'+GITHUB+'.sslVerify=true']
            if self.github_proxy:
                options += ['-c', 'http.'+GITHUB+'.proxy='+self.github_proxy]
        try:
            result = subprocess.run(['git', *options, *args], cwd=self.root, env=env,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncError('Git operation unavailable or timed out; no success assumed') from exc
        if check and result.returncode:
            # Credential helpers and vendor errors can contain secrets. Do not echo stderr.
            raise SyncError(f'Git {args[0]} failed (exit {result.returncode}); check transport/authentication and repository permissions')
        return result

    def text(self, *args, **kwargs):
        return self.call(*args, **kwargs).stdout.strip()


def refs(git, url, endpoint=None):
    return {ref:oid for oid,ref in (line.split('\t') for line in
            git.text('ls-remote', '--heads', '--tags', url, endpoint=endpoint).splitlines())}


def clean_main(git, expected=None):
    if git.text('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
        raise SyncError('Run from main; detached or other branches are not published')
    if git.text('status', '--porcelain', '--untracked-files=no'):
        raise SyncError('Tracked worktree/index changes must be reviewed and committed first')
    head = git.text('rev-parse', 'HEAD')
    if expected is not None and head != expected:
        raise SyncError('main changed during synchronization; retry from a stable checkout')
    return head


def reviewed_public_ca(git, oid, path):
    """Only the audited public CA is publishable, including every historical blob."""
    if path != 'src/client/sdk/ioalab_sdk/runtime/trust/ioalab-ca.pem':
        return False
    if not 0 < int(git.text('cat-file', '-s', oid)) <= 16384:
        return False
    pem = git.text('cat-file', 'blob', oid)
    certificate = re.fullmatch(r'-----BEGIN CERTIFICATE-----\r?\n([A-Za-z0-9+/=\r\n]+)-----END CERTIFICATE-----', pem)
    if certificate is None:
        return False
    try:
        der = base64.b64decode(''.join(certificate[1].splitlines()), validate=True)
    except ValueError:
        return False
    return hashlib.sha256(der).hexdigest() == '8d9dcaea6e34f557ab8cad9047babf75e181c4d90ac641a629021859b56dd686'


def check_history_paths(git, head):
    candidates = []
    for row in git.text('rev-list', '--objects', head).splitlines():
        oid, _, path = row.partition(' ')
        parts = path.lower().split('/')
        if (parts[0] in ('data', 'temp', 'apikey') or
                parts[-1] in ('.env', 'settings.local.json', 'llm_apikey.json', 'llm_apikey') or
                # Only development skills are public under .agents; keys, server access and data stay local.
                (parts[0] == '.agents' and len(parts) > 1 and parts[1] != 'skills') or
                (path.lower().endswith(('.pem', '.key')) and not reviewed_public_ca(git, oid, path))):
            candidates.append(path)
    if candidates:
        raise SyncError('Historical private-path candidates require review; refusing publication: ' +
                        ', '.join(sorted(set(candidates))[:10]))


def plan(git, source, target, *, source_endpoint=None, target_endpoint=None):
    """Tests use owned local bare repositories; CLI fixes the two real endpoints."""
    head = clean_main(git)
    published = refs(git, source, source_endpoint)
    if published.get('refs/heads/main') != head:
        raise SyncError('Local main must exactly equal published GitHub main')
    git.call('fetch', '--no-tags', '--no-write-fetch-head', source, 'refs/heads/main', endpoint=source_endpoint)
    clean_main(git, head)
    check_history_paths(git, head)
    destination = refs(git, target, target_endpoint)
    old = destination.get('refs/heads/main')
    if old:
        git.call('fetch', '--no-tags', '--no-write-fetch-head', target, 'refs/heads/main', endpoint=target_endpoint)
        if git.call('merge-base', '--is-ancestor', old, head, check=False).returncode:
            raise SyncError('GitCode main diverged or is ahead; refusing history rewrite')
    updates = {} if old == head else {'refs/heads/main':head}
    tags = {ref:oid for ref,oid in published.items()
            if ref.startswith('refs/tags/design-v') and not ref.endswith('^{}')}
    if tags:
        # One fetch for all tag objects; no per-tag network round trips or local tag writes.
        git.call('fetch', '--no-tags', '--no-write-fetch-head', source, *sorted(tags), endpoint=source_endpoint)
    selected = {}
    for ref,oid in sorted(tags.items()):
        commit = published.get(ref+'^{}', oid)
        if git.call('merge-base', '--is-ancestor', commit, head, check=False).returncode:
            continue
        selected[ref] = oid
        if ref in destination and destination[ref] != oid:
            raise SyncError('Conflicting GitCode design tag: '+ref)
        if ref not in destination:
            updates[ref] = oid
    return head, updates, selected


def synchronize(git, source, target, *, dry_run=False, source_endpoint=None, target_endpoint=None):
    head, updates, selected = plan(git, source, target, source_endpoint=source_endpoint, target_endpoint=target_endpoint)
    clean_main(git, head)
    published = refs(git, source, source_endpoint)
    expected = {**selected, 'refs/heads/main':head}
    if any(published.get(ref) != oid for ref,oid in expected.items()):
        raise SyncError('Published main or selected design tags moved during planning; retry')
    result = {'sourceCommit':head, 'dryRun':dry_run, 'updates':updates}
    if dry_run:
        result['status'] = 'planned'
        return result
    if updates:
        git.call('push', '--atomic', target, *[oid+':'+ref for ref,oid in updates.items()], endpoint=target_endpoint)
    actual = refs(git, target, target_endpoint)
    if any(actual.get(ref) != oid for ref,oid in expected.items()):
        raise SyncError('Remote verification did not match; result unknown, inspect before retrying')
    result['status'] = 'synchronized'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='read and validate refs without pushing')
    parser.add_argument('--github-proxy', help='optional credential-free HTTP(S) proxy for GitHub only; otherwise use normal Git transport settings')
    args = parser.parse_args()
    git = Git(Path(__file__).resolve().parents[1], args.github_proxy)
    try:
        if args.github_proxy:
            proxy = urlsplit(args.github_proxy)
            if (proxy.scheme not in ('http','https') or not proxy.hostname or proxy.username or proxy.password
                    or proxy.query or proxy.fragment or proxy.path not in ('','/')):
                raise SyncError('Use a credential-free HTTP(S) GitHub proxy URL')
        configured = git.text('remote', 'get-url', 'origin')
        parsed = urlsplit(configured)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment
                or configured.lower() != GITHUB.lower()):
            raise SyncError('origin must be the expected credential-free GitHub HTTPS repository')
        print(json.dumps(synchronize(git, GITHUB, GITCODE, dry_run=args.dry_run,
                                    source_endpoint='github', target_endpoint='gitcode'), ensure_ascii=False, indent=2))
        return 0
    except SyncError as exc:
        print(json.dumps({'status':'failed', 'message':str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
