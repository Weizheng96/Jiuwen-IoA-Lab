"""The six managed runtime units use installed packages and explicit role contexts."""
import json
from pathlib import PurePosixPath
import uuid


def quoted(value):
    value=str(value)
    if any(ord(c)<32 or c in '$%\\' for c in value):
        raise ValueError('Managed unit arguments cannot contain control characters, expansion or backslashes')
    return json.dumps(value,ensure_ascii=False)


def command(arguments):
    if not arguments or not PurePosixPath(arguments[0]).is_absolute():
        raise ValueError('Use the absolute installed interpreter')
    return ' '.join(quoted(value) for value in arguments)


def plans(layout, *, administrator):
    stable=layout.environments/'current'
    participant=stable/'participant/bin/python'
    swarm=stable/'swarm/bin/python'
    agents=layout.state/'agents'
    configuration=layout.config/'agents'
    official=agents/'official'
    def python(executable,module,*args):return [str(executable),'-I','-X','utf8','-m',module,*map(str,args)]
    rows={
        'core':{'unit':'ioalab-core.service','user':layout.core_user,'directory':layout.state/'core',
            'environment':layout.config/'core/core.env','after':[],
            'command':python(stable/'core/bin/python','ioalab','serve'),'pre':[]},
        'official':{'unit':'ioalab-official.service','user':layout.agents_user,'directory':official,
            'environment':configuration/'official.env','after':['core'],
            'command':python(participant,'ioalab_services','serve','--config',configuration/'official.json'),
            'pre':[python(participant,'ioalab_sdk.runtime','login','--config',
                          official/'accounts'/('ISP_'+str(i).zfill(3))/'config.json') for i in range(1,9)]}}
    if administrator:
        for key,module,after in (
            ('swarm-agent','jiuwenswarm.server.app_agentserver',[]),
            ('swarm-gateway','jiuwenswarm.gateway.app_gateway',['swarm-agent']),
            ('swarm-web','jiuwenswarm.channels.web.app_web',['swarm-gateway'])):
            rows[key]={'unit':'ioalab-'+key+'.service','user':layout.agents_user,
                'directory':agents/'swarm-admin','environment':configuration/'swarm-admin.env',
                'after':after,'command':python(swarm,module),'pre':[],'kill_mode':'control-group'}
        runtime=agents/'admin-assistant/config.json'
        rows['assistant']={'unit':'ioalab-admin-assistant.service','user':layout.agents_user,
            'directory':agents/'admin-assistant','environment':configuration/'assistant.env',
            'after':['core','swarm-web'],'command':python(participant,'ioalab_jiuwen','serve','--config',runtime),
            'pre':[python(participant,'ioalab_sdk.runtime','login','--config',runtime)]}
    return rows


def render(layout, deployment_id, plans):
    if not isinstance(deployment_id,str) or uuid.UUID(deployment_id).hex!=deployment_id:
        raise ValueError('Use the recorded deployment UUID')
    result={}
    for key,row in plans.items():
        after=['network-online.target',*[plans[name]['unit'] for name in row['after']]]
        lines=['# ioalab-deployment:'+deployment_id,'[Unit]',
            'Description=Jiuwen IoA Lab '+key,'After='+' '.join(after),
            '[Service]','Type=exec','User='+row['user'],'Group='+row['user'],
            'WorkingDirectory='+quoted(row['directory']),
            'EnvironmentFile='+quoted(row['environment']),'UMask=0077','NoNewPrivileges=yes','PrivateTmp=yes',
            *('ExecStartPre='+command(argv) for argv in row['pre']),
            'ExecStart='+command(row['command']),
            'KillMode='+row.get('kill_mode','mixed'),'SendSIGKILL=no','TimeoutStartSec=infinity','TimeoutStopSec=infinity',
            'Restart=on-failure','RestartSec=5','[Install]','WantedBy=multi-user.target','']
        result[row['unit']]='\n'.join(lines)
    return result


STOP_GROUPS=(('official','assistant'),('swarm-web','swarm-gateway','swarm-agent'),('core',))
START_ORDER=('core','swarm-agent','swarm-gateway','swarm-web','official','assistant')
