"""Filming-only local configuration editor; writes the actual skill file, never host settings."""
import json
from pathlib import Path

KEYS=('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD')


def editor_html(token):
    return r'''<!doctype html><meta charset="utf-8"><style>body{background:#0b1424;color:#dcecf5;font:24px "Microsoft YaHei";padding:54px}h1{font-size:38px;color:#8cf4d1}p{color:#9cb6c8;font-size:21px}textarea{width:100%;height:360px;background:#132235;color:#e7f3fb;border:1px solid #426377;border-radius:12px;font:24px Consolas;padding:25px;line-height:1.8}button{background:#72e9c7;border:0;border-radius:8px;padding:14px 36px;font-size:24px;margin-top:22px}#status{color:#8cf4d1}</style><h1>配置 IoA个人助手</h1><p>录制用本地文件编辑器 · 实际保存到技能目录 settings.local.json</p><textarea id="configuration" spellcheck="false">{
  "IOALAB_BASE_URL": "填写平台地址",
  "IOALAB_USERNAME": "填写平台账号",
  "IOALAB_PASSWORD": "填写账号密码"
}</textarea><button id="save">保存配置</button><p id="status"></p><script>save.onclick=async()=>{try{const r=await fetch('/save-settings',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Editor-Token':TOKEN},body:configuration.value});const v=await r.json();if(!r.ok)throw Error(v.error);document.getElementById('status').textContent='已保存并重新读取核对 · '+v.file}catch(e){document.getElementById('status').textContent=e.message}};</script>'''.replace('TOKEN',json.dumps(token))


def write_settings(cfg,data):
    if not cfg.get('onboarding') or cfg.get('scenario')!='delegation':raise ValueError('Only the explicit local onboarding recording can write settings')
    expected=dict(zip(KEYS,(cfg['base'],cfg['accounts']['b']['username'],cfg['accounts']['b']['password'])))
    expected.update(cfg.get('delegationPolicy',{}))
    if data!=expected:raise ValueError('Only the declared demonstration account and preference fields are accepted')
    skill=Path(cfg['skillDirectory']).resolve()
    target=skill/'settings.local.json'
    values={**data,'IOALAB_HOME':str(Path(cfg['folder']).resolve()/'client-b')}
    target.write_text(json.dumps(values,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if json.loads(target.read_text(encoding='utf-8'))!=values:raise RuntimeError('Configuration verification failed')
    return {'file':'settings.local.json','verified':True}
