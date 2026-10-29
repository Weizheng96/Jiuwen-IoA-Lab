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

def configure_skill_discovery(swarm):
    """Use pinned-host native skill selection and concrete tool schemas, without upstream edits."""
    import yaml
    path=Path(swarm)/'config/config.yaml'
    if not path.exists(): return
    original=path.read_text(encoding='utf-8')
    config=yaml.safe_load(original) or {}
    react=config.setdefault('react',{})
    if react.get('skill_mode')=='all' and config.get('progressive_tool_enabled') is False: return
    react['skill_mode']='all'
    config['progressive_tool_enabled']=False
    backup=path.with_name('config.before-ioa-discovery.yaml')
    if not backup.exists(): backup.write_text(original,encoding='utf-8')
    path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')


def configure_ioa_instructions(swarm):
    """Maintain only our marked block in the host's supported personal-instructions file."""
    path=Path(swarm)/'agent/workspace/AGENT.md'
    skill=(path.parent/'skills/ioalab-delegate/SKILL.md').resolve().as_posix()
    start='<!-- ioalab:begin -->';end='<!-- ioalab:end -->'
    block=f'''{start}
## IoA专业服务委托

用户提出专业服务需求并以虚拟币为预算时，必须先加载本地IoA技能；不要自行编程或写方案冒充委托。
第一步调用的工具只能是 skill_tool，参数为 {{"skill_name":"ioalab-delegate"}}。
ioalab-delegate 是已安装技能的名称，绝对不是工具名；禁止调用名为 ioalab-delegate 的工具。
若skill_tool不可用，用read_file读取 {skill} 后按文档执行；不要在线搜索同名技能，不推断技能未安装。
加载后始终调用文档中的delegate阶段入口；完整展示userMessage，四处确认必须等待用户下一条消息，working继续poll。禁止自行授权或伪造完成。
{end}'''
    original=path.read_text(encoding='utf-8') if path.exists() else '# 个人助手\n'
    if (start in original)!=(end in original):raise ValueError('Incomplete IoA personal instruction block')
    if start in original:
        a=original.index(start);b=original.index(end)+len(end)
        updated=original[:a]+block+original[b:]
    else:updated=original.rstrip()+'\n\n'+block+'\n'
    if original!=updated:
        backup=path.with_name('AGENT.before-ioa.md')
        if path.exists() and not backup.exists():backup.write_text(original,encoding='utf-8')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(updated,encoding='utf-8')


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
    configure_skill_discovery(swarm)
    configure_ioa_instructions(swarm)
    config=library/'ioalab-delegate/settings.local.json'
    # Preserve a deliberately configured existing skill account during upgrades.
    if not config.exists():
        settings={k:values[k] for k in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_HOME')}
        if values.get('IOALAB_SCHEDULE_FILE'):settings['IOALAB_SCHEDULE_FILE']=values['IOALAB_SCHEDULE_FILE']
        config.write_text(json.dumps(settings,indent=2)+'\n',encoding='utf-8')
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
