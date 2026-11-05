"""Bind the released personal-assistant Skill to an owned native installation.

Uses the original Web upload API and the original resident SDK, without reading
account configuration contents or implementing SDK operations in the deployer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid
import zipfile

from .processes import child_environment
from .release import checked, load, read_json
from .workspace import path_without_links, save


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def package_files(path):
    result={}
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name=PurePosixPath(entry.filename)
            if (name.is_absolute() or '\\' in entry.filename or ':' in entry.filename
                    or '..' in name.parts or name.parts[0]!='ioalab-delegate'
                    or stat.S_ISLNK(entry.external_attr>>16)):
                raise ValueError('Invalid personal-assistant Skill archive path')
            if entry.is_dir():continue
            relative=PurePosixPath(*name.parts[1:]).as_posix()
            if relative in result or relative=='scripts/deployment.local.json':
                raise ValueError('Duplicate or instance-private Skill asset')
            result[relative]=archive.read(entry)
    required={'SKILL.md','scripts/ioalab.py','scripts/installer.py','assets/runtime.json','assets/skill.json'}
    if not required<=result.keys() or json.loads(result['assets/skill.json'])!={'role':'assistant'}:
        raise ValueError('Use the complete released personal-assistant Skill')
    return result


def verify_files(folder, expected):
    for name,content in expected.items():
        path=path_without_links(folder/name)
        if not path.is_file() or path.read_bytes()!=content:
            raise RuntimeError('The imported personal-assistant Skill differs from its release')


def upload(url, path):
    target=urlsplit(url)
    if (target.scheme!='http' or target.hostname!='127.0.0.1' or not target.port
            or target.username or target.password or target.query or target.fragment or target.path not in ('','/')):
        raise ValueError('Use the owned loopback original Web URL')
    boundary='ioalab-'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ioalab-assistant-skill.zip"\r\n'
          'Content-Type: application/zip\r\n\r\n').encode()+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    request=Request(url.rstrip('/')+'/file-api/skills/import',data=body,
                    headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=90) as response:
        value=json.load(response)
    if value.get('success') is not True or value.get('skill',{}).get('name')!='ioalab-delegate':
        raise RuntimeError('Original native Skill import did not complete')
    return value


def sdk_status(command):
    result=subprocess.run(list(map(str,command)),env=child_environment(),capture_output=True,text=True,
                          encoding='utf-8',timeout=45)
    try:value=json.loads(result.stdout)
    except ValueError:raise RuntimeError('SDK returned no complete public status receipt') from None
    if result.returncode or value.get('status')!='completed':
        raise RuntimeError('Original personal-assistant SDK status is unavailable')
    return value['data']


def ensure_assistant_skill(release, participant_python, workspace, configuration, native_url, boot_id):
    """Idempotent original import plus exact release/SDK/boot binding checks."""
    release=path_without_links(release);workspace=path_without_links(workspace)
    configuration=path_without_links(configuration);participant_python=path_without_links(participant_python)
    if configuration!=workspace.parent/'admin-assistant/config.json' or not configuration.is_file():
        raise ValueError('Bind only the owned administrator assistant configuration')
    document=load(release)
    archive=checked(release,document['assets']['assistantSkill']);expected=package_files(archive)
    prefix=participant_python.parent.parent
    installation=read_json(prefix/'deployment.json')
    identifier=installation['environmentId']
    if (installation['identity'].get('unit')!='participant'
            or installation['identity']['plan']!=document['units']['participant']):
        raise RuntimeError('Use the released participant environment')
    folder=path_without_links(workspace/'agent/workspace/skills/ioalab-delegate')
    if not folder.exists():
        receipt=upload(native_url,archive)
        if path_without_links(receipt['skill']['workspace_path'])!=folder:
            raise RuntimeError('Original Skill import selected another workspace')
    verify_files(folder,expected)
    binding={'format':'ioalab.deployed-skill/1','python':str(participant_python),'configuration':str(configuration),
             'environmentId':identifier,'manifestSha256':hashlib.sha256(expected['assets/runtime.json']).hexdigest()}
    locator=path_without_links(folder/'scripts/deployment.local.json')
    if locator.exists():
        if read_json(locator)!=binding:raise RuntimeError('Preserve the original Skill deployment binding')
    else:save(locator,binding)
    # The actual product entry verifies deployed wheel digests and asks that SDK,
    # rather than a copied bridge, for the existing resident's current status.
    live=sdk_status([participant_python,'-I','-X','utf8',folder/'scripts/ioalab.py','runtime.status'])
    identities=live.get('identities',[])
    if (live.get('bootId')!=boot_id or not boot_id or not live.get('platformConnected')
            or live.get('account',{}).get('username')!='ISC_001'
            or not live.get('host',{}).get('available')
            or live.get('installation',{}).get('releaseId')!=identifier
            or len(identities)!=1 or identities[0].get('profile')!='admin'
            or identities[0].get('role')!='assistant'):
        raise RuntimeError('Product Skill did not reach the original ready assistant runtime')
    return {'skill':'ioalab-delegate','assetSha256':document['assets']['assistantSkill']['sha256'],
            'environmentId':identifier,'bootId':boot_id,'sameResidentVerified':True,
            'nativeSkillList':'unverified','browserA2UI':'unverified'}
