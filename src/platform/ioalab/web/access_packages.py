"""Discovery of the complete skill archives actually present in this deployment."""
import hashlib
import io
import json
import zipfile


def packages(directory):
    result=[]
    for role,product,name in [('assistant','ioalab-delegate','assistant'),('service','ioalab-provider','provider')]:
        filename=f'ioalab-{name}-skill.zip'
        try:
            blob=(directory/filename).read_bytes()
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                required={f'{product}/{path}' for path in ('SKILL.md','scripts/ioalab.py','scripts/installer.py','assets/runtime.json','assets/skill.json','assets/requirements.lock')}
                if not required.issubset(archive.namelist()):continue
                manifest=json.loads(archive.read(f'{product}/assets/runtime.json'))
                if manifest.get('format')!=1 or not isinstance(manifest.get('skillVersion'),str):continue
                if json.loads(archive.read(f'{product}/assets/skill.json')).get('role')!=role:continue
                if hashlib.sha256(archive.read(f'{product}/assets/requirements.lock')).hexdigest()!=manifest.get('constraintsSha256'):continue
                wheels=manifest.get('wheels')
                if not isinstance(wheels,list) or not wheels:continue
                if any(not isinstance(wheel,dict) or not isinstance(wheel.get('file'),str) or '/' in wheel['file'] or '\\' in wheel['file'] or
                    hashlib.sha256(archive.read(f'{product}/assets/wheels/'+wheel['file'])).hexdigest()!=wheel.get('sha256') for wheel in wheels):continue
        except (OSError,ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile):continue
        result.append({'role':role,'skill':product,'url':'/downloads/'+filename,'sha256':hashlib.sha256(blob).hexdigest(),
            'size':len(blob),'skillVersion':manifest['skillVersion'],'runtimeFormat':1,'python':'3.11'})
    return {'format':1,'defaultRole':'assistant','packages':result,'nextAction':'download_verify_then_native_import_and_load'}
