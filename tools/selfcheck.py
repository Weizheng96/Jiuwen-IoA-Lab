"""Architecture, content and real-process protocol checks; no model calls."""
import ast
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN=('托管','代收代付','账户余额','清算','结算通道','按裁定强制划转','资金沉淀')
ENV={**os.environ,'PYTHONUTF8':'1','IOALAB_STUB':'1','IOALAB_SEED_FILE':str(ROOT/'deploy/default-seed.json')}


def main():
    failures=[]
    def ok(name,condition):
        print(('  ✓ ' if condition else '  ✗ ')+name,flush=True)
        if not condition: failures.append(name)
    for label,command in [('Design versions',[sys.executable,'设计/check.py']),
                          ('Demo scripts and published evidence',[sys.executable,'demo/script_documents.py','--check']),
                          ('Current documentation links',[sys.executable,'tools/check_docs.py']),
                          ('Official service definitions',[sys.executable,'tools/check_services.py']),
                          ('Vendored deployment inputs',[sys.executable,'tools/check_vendor.py']),
                          ('Public initialization manifest',[sys.executable,'tools/build_seed.py','--check']),
                          ('Three-process smoke',[sys.executable,'tools/smoke.py'])]:
        result=subprocess.run(command,cwd=ROOT,env=ENV,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=300)
        ok(label,result.returncode==0)
        if result.returncode: print(result.stdout[-3000:]+result.stderr[-3000:])
    source=json.loads((ROOT/'vendor/wheels/SOURCE.json').read_text(encoding='utf-8'))
    wheel=ROOT/'vendor/wheels'/source['wheel']
    ok('A2C wheel digest unchanged',hashlib.sha256(wheel.read_bytes()).hexdigest()==source['sha256'])
    imports=[]
    for path in (ROOT/'src/platform/ioalab').rglob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and (node.module or '') in ('a2c.client','a2c.provider'):
                imports.append(str(path.relative_to(ROOT)))
            if isinstance(node,ast.Import) and any(a.name.startswith(('jiuwenswarm','openjiuwen')) for a in node.names):
                imports.append(str(path.relative_to(ROOT)))
    ok('Core imports no participant SDK or agent runtime',not imports)
    hits=[]
    for folder in ['src/platform/ioalab','src/access/sdk','official-services','connector','experiments','设计','docs','deploy','examples']:
        for path in (ROOT/folder).rglob('*'):
            if path.suffix not in ('.py','.md','.js','.html','.json','.txt','.sh','.example','.service'): continue
            if any(part in ('__pycache__','变更','*.egg-info') or part.endswith('.egg-info') for part in path.parts): continue
            if path.name=='test_boundary.py': continue
            content=path.read_text(encoding='utf-8')
            if any(word in content for word in FORBIDDEN): hits.append(str(path.relative_to(ROOT)))
    ok('Current product sources pass wording checks',not hits)
    if hits: print('\n'.join(hits))
    result=subprocess.run(['git','-c',f'safe.directory={ROOT.as_posix()}','ls-files','data','logs'],cwd=ROOT,capture_output=True,text=True)
    ok('Runtime data is not tracked',result.returncode==0 and not result.stdout.strip())
    if shutil.which('node'):
        result=subprocess.run(['node','tools/check_admin.cjs'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        ok('Admin external identities and signed evidence',result.returncode==0)
        if result.returncode: print(result.stdout+result.stderr)
    print(('✓ All checks passed' if not failures else '✗ Failed: '+', '.join(failures)),flush=True)
    return int(bool(failures))


if __name__=='__main__': raise SystemExit(main())
