"""Bind real root-Chrome evidence to one immutable engineering build context."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

CHECKS=('originalUiProxy','originalWsProxy','a2ui','skillSdkCall')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context_inputs(context,source):
    context=Path(context).resolve()
    if not re.fullmatch('[0-9a-f]{40}',source):raise ValueError('Use the exact published source commit')
    files={}
    for path in sorted(context.rglob('*')):
        relative=path.relative_to(context)
        if relative.parts[0]=='acceptance':continue
        if path.is_symlink():raise ValueError('Image input cannot be a link')
        if path.is_file():files[relative.as_posix()]=sha(path)
    if not files or 'Dockerfile' not in files:raise ValueError('Use the complete immutable image context')
    digest=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    value={'format':'ioalab.trial-image-inputs/1','sourceRevision':source,'contextSha256':digest}
    folder=context/'acceptance';folder.mkdir(exist_ok=True)
    path=folder/'inputs.json'
    if path.exists() and json.loads(path.read_text())!=value:
        raise ValueError('Original image inputs changed; use a new reviewed context')
    path.write_text(json.dumps(value,sort_keys=True)+'\n')
    return value


def record(context,review):
    context=Path(context).resolve();review=Path(review).resolve()
    inputs=json.loads((context/'acceptance/inputs.json').read_text())
    if context_inputs(context,inputs['sourceRevision'])!=inputs:raise ValueError('Original context changed')
    value=json.loads(review.read_text())
    if (value.get('reviewedBy')!='root-chrome' or not value.get('browserSessionId')
            or value.get('sourceRevision')!=inputs['sourceRevision']
            or value.get('contextSha256')!=inputs['contextSha256']
            or not re.fullmatch('sha256:[0-9a-f]{64}',value.get('engineeringImageId',''))):
        raise ValueError('Actual root-Chrome review for this engineering image is required')
    checks={};copies={}
    for check in CHECKS:
        item=value['checks'][check]
        if item.get('status')!='passed' or not item.get('artifacts'):
            raise ValueError('All four real browser checks must have passed evidence')
        artifacts={}
        for name,expected in item['artifacts'].items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Artifact path escaped review directory')
            path=review.parent/relative
            if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)) or sha(path)!=expected:
                raise ValueError('Original browser evidence changed or is absent')
            destination='evidence/'+check+'/'+relative.name
            if destination in copies and sha(copies[destination])!=expected:
                raise ValueError('Two acceptance artifacts have the same destination')
            artifacts[destination]=expected;copies[destination]=path
        checks[check]={'status':item['status'],'artifacts':artifacts}
    result={**{key:value[key] for key in ('reviewedBy','browserSessionId','engineeringImageId')},**inputs,
            'format':'ioalab.trial-native-acceptance/1','checks':checks}
    for name,path in copies.items():
        destination=context/'acceptance'/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,destination)
    target=context/'acceptance/native-acceptance.json'
    target.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('context','record'))
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--source');parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    result=context_inputs(args.context,args.source or '') if args.action=='context' else record(args.context,args.review)
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
