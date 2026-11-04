"""One-time removal of our old host overrides; preserve owner edits and backup files."""
import argparse
from pathlib import Path
import yaml


def restore(swarm):
    root=Path(swarm);changed=[];unresolved=[]
    path=root/'agent/workspace/AGENT.md'
    if path.exists():
        original=path.read_text(encoding='utf-8')
        start='<!-- ioalab:begin -->';end='<!-- ioalab:end -->'
        if (start in original)!=(end in original):
            raise ValueError('Incomplete IoA instruction block; inspect before changing owner instructions')
        if start in original:
            if original.count(start)!=1 or original.count(end)!=1:raise ValueError('Ambiguous IoA instruction blocks')
            a=original.index(start);b=original.index(end)+len(end)
            path.write_text(original[:a]+original[b:],encoding='utf-8');changed.append('AGENT.md: removed only marked IoA instructions')
    path=root/'config/config.yaml';backup=path.with_name('config.before-ioa-discovery.yaml')
    if backup.exists() and path.exists():
        current=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        before=yaml.safe_load(backup.read_text(encoding='utf-8')) or {}
        dirty=False
        for parent,key,ours in ((None,'progressive_tool_enabled',False),('react','skill_mode','all')):
            now=current.get(parent,{}) if parent else current
            old=before.get(parent,{}) if parent else before
            if key in now and now[key]==ours:
                if key in old:
                    if now[key]!=old[key]:now[key]=old[key];dirty=True
                else:del now[key];dirty=True
            elif now.get(key)!=old.get(key):unresolved.append('Preserved owner change: '+key)
        if dirty:
            path.write_text(yaml.safe_dump(current,allow_unicode=True,sort_keys=False),encoding='utf-8')
            changed.append('config.yaml: restored only matching IoA override keys')
    return {'changed':changed,'preserved':unresolved,'restartRequired':bool(changed)}


if __name__=='__main__':
    import json
    p=argparse.ArgumentParser();p.add_argument('--swarm-data',type=Path,required=True);a=p.parse_args()
    print(json.dumps(restore(a.swarm_data),ensure_ascii=False))
