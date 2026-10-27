"""Generate local deployment configuration; never print credentials or overwrite authorizations."""
import argparse,json,os,secrets,sys,zipfile
from pathlib import Path

def read_env(path):
    result={}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);v=v.strip()
            if v.startswith('"') and v.endswith('"'):
                try:v=json.loads(v)
                except json.JSONDecodeError:v=v[1:-1]
            elif v.startswith("'") and v.endswith("'"):v=v[1:-1]
            result[k.strip()]=v
    return result

def install_skill(root,swarm,values):
    bundle=root/'dist/ioalab-skill.zip'
    if not bundle.is_file(): raise RuntimeError('Run tools/build_connector.py before administrator configuration')
    library=(swarm/'agent/workspace/skills').resolve()
    with zipfile.ZipFile(bundle) as archive:
        for member in archive.infolist():
            if member.is_dir():continue
            relative=Path(member.filename)
            target=(library/relative).resolve()
            if not relative.parts or relative.parts[0]!='ioalab-delegate' or not target.is_relative_to(library): raise ValueError('Invalid skill bundle path')
            if target.name=='settings.local.json': raise ValueError('Skill bundle must not contain local credentials')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(member))
    config=library/'ioalab-delegate/settings.local.json'
    # Preserve a deliberately configured existing skill account during upgrades.
    if not config.exists():
        config.write_text(json.dumps({k:values[k] for k in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_HOME')},indent=2)+'\n',encoding='utf-8')
        try:os.chmod(config,0o600)
        except OSError:pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--core-env',required=True);p.add_argument('--assistant-env',required=True);p.add_argument('--swarm-data',required=True);p.add_argument('--assistant-data',required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1];core=Path(args.core_env);config=read_env(core)
    password=config.get('IOALAB_ASSISTANT_PASSWORD')
    if not password:
        password=secrets.token_urlsafe(18);lines=core.read_text(encoding='utf-8').splitlines();lines=[l for l in lines if not l.startswith('IOALAB_ASSISTANT_PASSWORD=')];lines.append('IOALAB_ASSISTANT_PASSWORD='+password);core.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dest=Path(args.assistant_env)
    values=read_env(dest) if dest.exists() else read_env(root/'deploy/personal-assistant.env.example')
    secret=values.get('IOALAB_SWARM_BRIDGE_SECRET') or secrets.token_urlsafe(32)
    values.update(IOALAB_BASE_URL=config['IOALAB_PUBLIC_URL'],IOALAB_USERNAME='ISC_001',IOALAB_PASSWORD=password,IOALAB_HOME=str(Path(args.assistant_data).resolve()),IOALAB_SWARM_BRIDGE_SECRET=secret)
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text('\n'.join(k+'='+json.dumps(v) for k,v in values.items())+'\n',encoding='utf-8')
    swarm=Path(args.swarm_data).resolve();env=swarm/'config/.env'
    if not env.exists(): raise RuntimeError('Initialize the pinned Jiuwen workspace before configuration')
    install_skill(root,swarm,values)
    settings=read_env(env);settings.update(EXTENSION_DIRS=str(root/'personal-assistant/extensions'),IOALAB_SWARM_BRIDGE_SECRET=secret,IOALAB_SWARM_USER_ID='ioa-admin')
    env.write_text('\n'.join(k+'='+json.dumps(v) for k,v in settings.items())+'\n',encoding='utf-8')
    for file in (dest,env):
        try: os.chmod(file,0o600)
        except OSError: pass
    print('Administrator account, notification bridge and environment files configured. Configure Jiuwen model and calendar/SMTP before external acceptance.')

if __name__=='__main__':main()
