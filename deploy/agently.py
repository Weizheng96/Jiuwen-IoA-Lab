"""Pinned standalone Tencent CLI, isolated from Python environments and runtime credentials."""
import base64
import hashlib
import io
import os
from pathlib import Path
import tarfile
import urllib.request

VERSION = '1.0.18'
URL = 'https://registry.npmjs.org/@tencent-qqmail/agently-cli-linux-x64/-/agently-cli-linux-x64-1.0.18.tgz'
INTEGRITY = '6+5aBFFoPtscK3jafN1jZ/OK3f4HK0dAKdmHnja71O7BiTGRAGIUe6Ygd/ysiqouPXkvedZpVbLlHxoykNgpgA=='


def authorization_paths(layout):
    # In pinned 1.0.18, AGENTLY_CLI_CONFIG_DIR only moves app configuration.
    # FileStorageDir keeps encrypted credentials/master keys in the OS HOME.
    home=layout.state/'agents'
    return (home/'agently-auth',home/'.local/share/agently-cli')


def unpack(content):
    if base64.b64encode(hashlib.sha512(content).digest()).decode() != INTEGRITY:
        raise ValueError('Official Agently CLI package integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        member = archive.getmember('package/bin/agently-cli')
        if not member.isfile() or member.size > 30_000_000:
            raise ValueError('Expected the pinned standalone Linux CLI')
        return archive.extractfile(member).read()


def ensure(layout):
    from .files import path_without_links, sha, save
    root = path_without_links(layout.root/'tools/agently'/VERSION)
    executable = root/'agently-cli'
    marker = root/'installation.json'
    if executable.exists():
        from .release import read_json
        record = read_json(marker)
        if record != {'version':VERSION,'url':URL,'sha256':sha(executable)}:
            raise ValueError('Installed Agently CLI differs from its pinned identity')
        return executable
    # Production fetch is direct; no developer VPN/HTTP proxy is inherited.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(URL, timeout=45) as response:
        content = response.read(20_000_001)
    binary = unpack(content)
    for directory in (layout.root/'tools', layout.root/'tools/agently', root):
        path_without_links(directory)
        directory.mkdir(mode=0o755, exist_ok=True)
        if os.name=='posix' and directory.stat().st_uid!=os.geteuid():
            raise ValueError('Agently tools directory is not owned by the installer')
        directory.chmod(0o755)
    with executable.open('xb') as stream:
        stream.write(binary); stream.flush(); os.fsync(stream.fileno())
    executable.chmod(0o755)
    save(marker, {'version':VERSION,'url':URL,'sha256':sha(executable)})
    return executable
