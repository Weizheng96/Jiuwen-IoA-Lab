"""Read immutable, source-bound browser acceptance evidence from the image."""
import hashlib
import json
from pathlib import Path
import re

CHECKS=('originalUiProxy','originalWsProxy','a2ui','skillSdkCall')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validated_checks(root=Path('/opt/trial/acceptance')):
    root=Path(root)
    try:
        inputs=json.loads((root/'inputs.json').read_text())
        receipt=json.loads((root/'native-acceptance.json').read_text())
        if (receipt.get('format')!='ioalab.trial-native-acceptance/1'
                or receipt.get('sourceRevision')!=inputs['sourceRevision']
                or receipt.get('contextSha256')!=inputs['contextSha256']
                or not re.fullmatch('[0-9a-f]{40}',inputs['sourceRevision'])
                or not re.fullmatch('[0-9a-f]{64}',inputs['contextSha256'])
                or not re.fullmatch('sha256:[0-9a-f]{64}',receipt.get('engineeringImageId',''))
                or receipt.get('reviewedBy')!='root-chrome'
                or not receipt.get('browserSessionId')):
            raise ValueError('Acceptance does not describe these image inputs')
        result={}
        for check in CHECKS:
            item=receipt['checks'][check];artifacts=item['artifacts']
            if item.get('status')!='passed' or not isinstance(artifacts,dict) or not artifacts:
                raise ValueError('A real reviewed browser check is missing')
            for name,digest in artifacts.items():
                path=root/name
                if (not name.startswith('evidence/') or '..' in Path(name).parts or Path(name).is_absolute()
                        or not path.is_file() or any(p.is_symlink() for p in (path,*path.parents))
                        or sha(path)!=digest):
                    raise ValueError('A reviewed acceptance artifact changed')
            result[check]=item['status']=='passed'
        return result
    except (OSError,ValueError,KeyError,TypeError):
        return {check:False for check in CHECKS}
