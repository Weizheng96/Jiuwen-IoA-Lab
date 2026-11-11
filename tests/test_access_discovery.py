import hashlib
import json
import zipfile


def test_discovery_reports_only_actual_complete_bundles(platform,client):
    root=platform.s.bundles_dir
    root.mkdir(parents=True,exist_ok=True)
    path=root/'ioalab-assistant-skill.zip'
    previous=path.read_bytes() if path.exists() else None
    try:
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('ioalab-delegate/assets/runtime.json',json.dumps({'format':1,'skillVersion':'0.0.151',
                'constraintsSha256':hashlib.sha256(b'lock').hexdigest(),'wheels':[{'file':'sdk.whl','sha256':hashlib.sha256(b'wheel').hexdigest()}]}))
            archive.writestr('ioalab-delegate/assets/requirements.lock',b'lock')
            archive.writestr('ioalab-delegate/assets/wheels/sdk.whl',b'wheel')
            archive.writestr('ioalab-delegate/assets/skill.json',json.dumps({'role':'assistant'}))
            archive.writestr('ioalab-delegate/SKILL.md','# skill')
            archive.writestr('ioalab-delegate/scripts/ioalab.py','# script')
            archive.writestr('ioalab-delegate/scripts/installer.py','# installer')
        response=client.get('/api/access/packages')
        assert response.status_code==200
        item=next(item for item in response.json()['packages'] if item['role']=='assistant')
        assert item['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
        assert item['url']=='/downloads/ioalab-assistant-skill.zip'
        assert item['skillVersion']=='0.0.151'
        path.write_bytes(b'not a complete zip')
        assert not any(item['role']=='assistant' for item in client.get('/api/access/packages').json()['packages'])
    finally:
        if previous is None:path.unlink(missing_ok=True)
        else:path.write_bytes(previous)
