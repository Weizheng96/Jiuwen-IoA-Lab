"""Public local installation metadata, independent of concrete hosts and tools."""
from importlib import metadata
import json
from pathlib import Path
import re
import sys

from .protocol import RuntimeFault


FORMAT='ioalab.access-installation/1'
SAFE_CALLS={'runtime.status','identity.status','confirmations.get','operations.get','runtime.open_ui','runtime.stop'}


def installed_version(name):
    if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',name):
        raise RuntimeFault('not_ready','Invalid installation package metadata','runtime.install')
    try:return metadata.version(name)
    except metadata.PackageNotFoundError:
        raise RuntimeFault('not_ready','Install the selected access distribution: '+name,'runtime.install') from None


def current(assembly=None):
    prefix=Path(sys.prefix).resolve();marker=prefix/'ioalab-release.json';ready=prefix/'.ready'
    versions={'ioalab-sdk':installed_version('ioalab-sdk')};release=None
    if marker.exists() or ready.exists():
        try:
            if marker.is_symlink() or ready.is_symlink() or not marker.is_file() or not ready.is_file():raise ValueError()
            if marker.stat().st_size>32768 or ready.stat().st_size>128:raise ValueError()
            value=json.loads(marker.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or set(value)!={'format','releaseId','versions'} or value['format']!=FORMAT:raise ValueError()
            if not isinstance(value['releaseId'],str) or not re.fullmatch('[a-f0-9]{64}',value['releaseId']):raise ValueError()
            if ready.read_text(encoding='utf-8')!=value['releaseId']:raise ValueError()
            expected=value['versions']
            if not isinstance(expected,dict) or not 1<=len(expected)<=64 or 'ioalab-sdk' not in expected:raise ValueError()
            if any(not isinstance(version,str) or not 1<=len(version)<=128 or installed_version(name)!=version for name,version in expected.items()):raise ValueError()
        except (OSError,ValueError,RuntimeFault):
            raise RuntimeFault('not_ready','Installed release identity is incomplete or changed; preserve its data and inspect the installation','runtime.install') from None
        release=value['releaseId'];versions=dict(expected)
    if assembly:
        version=installed_version(assembly)
        if release and versions.get(assembly)!=version:
            raise RuntimeFault('not_ready','The assembly is outside this installed release','runtime.install')
        versions[assembly]=version
    return {'format':1,'environment':str(prefix),'releaseId':release,'versions':versions,'assembly':assembly}


def require_matching(actual,*,assembly=None):
    if not isinstance(actual,dict) or actual!=current(assembly):
        raise RuntimeFault('installation_changed','Running instance uses another or unverified installation; safely stop it before starting this release','runtime.stop')
