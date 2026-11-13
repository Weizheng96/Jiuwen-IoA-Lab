"""Maintainer-only universal client dependency lock; no installed metadata."""
from email.parser import BytesParser
from pathlib import Path
import json,re,subprocess,tempfile,tomllib,zipfile
from packaging.requirements import Requirement
from packaging.markers import Marker
from packaging.utils import canonicalize_name
ROOT=Path(__file__).resolve().parents[1]
HOSTS={'jiuwen':'ioalab-jiuwen','codex':'ioalab-codex','claude_code':'ioalab-claude-code'}
EXTRA=re.compile(r'(?:(.*) and )?extra == "(jiuwen|codex|claude-code)"$')
def lines(path):
 return [Requirement(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip() and not s.lstrip().startswith('#')]
def host_requirement(req):
 marker=str(req.marker) if req.marker else '';m=EXTRA.fullmatch(marker)
 if not m:
  if 'extra' in marker:raise ValueError('Unexpected client extra expression')
  return None,req
 req.marker=Marker(m[1]) if m[1] else None
 return m[2].replace('-','_'),req

def constraints(*,root=ROOT,previous=None,work=None,index='https://pypi.tuna.tsinghua.edu.cn/simple',resolver='uv'):
 root=Path(root).resolve();local={};vendor={}
 for folder in [root/'src/client/sdk',*(root/'src/client/hosts'/h for h in HOSTS)]:
  p=tomllib.loads((folder/'pyproject.toml').read_text(encoding='utf-8'))['project'];local[canonicalize_name(p['name'])]=(p,(folder/'VERSION').read_text().strip().removeprefix('v'))
 for item in json.loads((root/'vendor/SOURCES.json').read_text())['components']:
  if item['id'] not in ('a2c','jiuwenswarm-process-sdk'):continue
  info=item['wheelInfo']
  with zipfile.ZipFile(root/'vendor'/info['file']) as z:meta=BytesParser().parsebytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
  vendor[canonicalize_name(info['package'])]=(info['version'],meta.get_all('Requires-Dist',[]))
 excluded=set(local)|set(vendor)|{'ioalab-official'}
 def external(values):return [v for v in values if canonicalize_name(Requirement(v).name) not in excluded and 'extra ==' not in v]
 base=external(local['ioalab-sdk'][0]['dependencies'])+external(vendor['a2c'][1]);groups={'core':base}
 for host,name in HOSTS.items():
  groups[host]=base+external(local[name][0]['dependencies'])
  if host=='jiuwen':groups[host]+=external(vendor['jiuwenswarm-process-sdk'][1])
 with tempfile.TemporaryDirectory(prefix='ioa-client-lock-') as temporary:
  folder=Path(work or temporary);folder.mkdir(parents=True,exist_ok=True);old=[]
  for req in lines(previous or root/'src/client/skills/_shared/requirements.lock'):
   _,req=host_requirement(req)
   if canonicalize_name(req.name) not in excluded:old.append(str(req))
  (folder/'previous.txt').write_text('\n'.join(old)+'\n',encoding='utf-8')
  def compile_group(name,values,locked):
   source=folder/(name+'.in');target=folder/(name+'.txt');source.write_text('\n'.join(sorted(set(values)))+'\n',encoding='utf-8')
   result=subprocess.run([resolver,'pip','compile','--universal','--python-version','3.11','--no-header','--no-annotate','--index-url',index,'--constraint',str(locked),'--output-file',str(target),str(source)],capture_output=True,text=True,encoding='utf-8')
   (folder/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
   if result.returncode:raise RuntimeError('Universal dependency resolution failed: '+name)
   return lines(target)
  compile_group('union',[v for values in groups.values() for v in values],folder/'previous.txt')
  core=compile_group('core',base,folder/'union.txt');core_keys={str(r) for r in core};output=[str(r) for r in core]
  output+=['ioalab-sdk=='+local['ioalab-sdk'][1],'a2c=='+vendor['a2c'][0]]
  for host,name in HOSTS.items():
   requirements=[r for r in compile_group(host,groups[host],folder/'union.txt') if str(r) not in core_keys];requirements.append(Requirement(name+'=='+local[name][1]))
   if host=='jiuwen':requirements.append(Requirement('jiuwenswarm-process-sdk=='+vendor['jiuwenswarm-process-sdk'][0]))
   for req in requirements:
    guard='extra == "'+host+'"';req.marker=Marker('('+str(req.marker)+') and '+guard if req.marker else guard);output.append(str(req))
  return '\n'.join(sorted(set(output),key=str.casefold))+'\n'
