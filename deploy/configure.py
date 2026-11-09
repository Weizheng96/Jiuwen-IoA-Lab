"""Claim a deployment and create its one private platform input before installation."""
import argparse
import json
import os
from pathlib import Path
import secrets

from .layout import Layout,write_initial
from .release import read_json


def initialize(layout,*,llm=None):
    if llm is not None:
        from .configuration import model
        llm=model(llm)
    layout.claim()
    path=layout.config/'core/platform.json'
    if path.exists():raise ValueError('Platform configuration already exists; edit the original private file')
    directory=path.parent
    if directory.exists() and any(directory.iterdir()):raise ValueError('Preserve the existing core configuration and import it explicitly')
    directory.mkdir(mode=0o700,exist_ok=True)
    os.chmod(directory,0o700)
    value=read_json(Path(__file__).with_name('platform.json.example'))
    if llm is not None:
        value['llm'].update(model=llm['name'],base_url=llm['base_url'],api_key=llm['api_key'])
    assets=layout.environments/'current/release'
    value['runtime'].update(data=str(layout.state/'core'),seed_file=str(assets/'seed.json'),
        bundles_dir=str(assets/'skills'),session_secret=secrets.token_hex(32),assistant_password=secrets.token_urlsafe(24))
    write_initial(path,(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/opt/ioalab-runtime'))
    parser.add_argument('--state',type=Path,default=Path('/var/lib/ioalab'))
    parser.add_argument('--config-dir',type=Path,default=Path('/etc/ioalab'))
    args=parser.parse_args(argv)
    try:
        path=initialize(Layout(args.root,args.state,args.config_dir))
        print(json.dumps({'status':'completed','platformConfig':str(path)},ensure_ascii=False));return 0
    except Exception as error:
        print(json.dumps({'status':'not_ready','error':{'type':type(error).__name__}},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
