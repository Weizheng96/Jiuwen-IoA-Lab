"""Discovery of role-only skills; the single client is installed from GitCode."""
import hashlib
import json
import zipfile
import io


def packages(directory):
    result=[]
    for role,product,name in [('assistant','ioalab-delegate','assistant'),('service','ioalab-provider','provider')]:
        filename=f'ioalab-{name}-skill.zip'
        try:
            blob=(directory/filename).read_bytes()
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                required={f'{product}/{path}' for path in ('SKILL.md','scripts/ioalab.py','references/setup.md')}
                if not required.issubset(archive.namelist()):continue
                if any('/assets/wheels/' in path or path.endswith('/installer.py') for path in archive.namelist()):continue
        except (OSError,ValueError,zipfile.BadZipFile):continue
        result.append({'role':role,'skill':product,'url':'/downloads/'+filename,'sha256':hashlib.sha256(blob).hexdigest(),
                       'size':len(blob),'kind':'role-skill','python':'3.11'})
    return {'format':2,'client':{'package':'ioalab-client','source':'gitcode','subdirectory':'src/client'},
            'packages':result,'nextAction':'install_client_from_git_tag_then_export_role_skills'}
