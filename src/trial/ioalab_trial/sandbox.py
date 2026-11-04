"""Fixed Docker targets. The host coordinator never mounts or reads business volumes."""
import hashlib
import hmac
import json
import re
import subprocess
import time
from pathlib import Path
import httpx


class DockerSandbox:
    def __init__(self, config):
        self.config = config
        if not re.fullmatch(r'[a-z][a-z0-9-]{2,30}',config['deployment']):
            raise ValueError('Invalid deployment identity')
        if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}',config['image']):
            raise ValueError('Pin the complete reviewed image digest')
        if len(config['control_key']) < 32:
            raise ValueError('Dedicated high entropy sandbox control key required')
        model = config.get('model',{})
        from .model_egress import provider_base
        if set(model)!={'name'} or not isinstance(model['name'],str) or not model['name']:
            raise ValueError('Only the model name enters the seat; provider keys belong exclusively to its sidecar')
        provider_base(config['provider_base_url'])
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',config.get('egress_network','')):
            raise ValueError('Configure the fixed provider egress bridge')
        self.secret_path=Path(config['proxy_secret_file'])
        if not self.secret_path.is_absolute() or '..' in self.secret_path.parts or ',' in str(self.secret_path):
            raise ValueError('Use an absolute dedicated provider secret path')
        self.run = subprocess.run

    def identity(self, seat, epoch):
        if seat not in (1,2,3) or not isinstance(epoch,str) or not re.fullmatch('[a-f0-9]{32}',epoch):
            raise ValueError('Invalid sandbox ownership')
        return self.config['deployment'] + '-' + str(seat) + '-' + epoch

    def command(self, *args):
        result = self.run(['docker',*args],capture_output=True,text=True,timeout=90)
        if result.returncode:
            raise RuntimeError('Sandbox command failed; seat remains quarantined')
        return result.stdout

    def inspect(self, kind, name):
        return json.loads(self.command(kind,'inspect',name))[0]

    def owned(self, kind, name):
        value = self.inspect(kind,name)
        labels = value.get('Config',{}).get('Labels',{}) if kind == 'container' else value.get('Labels',{})
        if labels.get('ioalab.trial.owner') != name:
            raise RuntimeError('Sandbox ownership mismatch')
        if kind == 'container':
            mounts = value.get('Mounts',[])
            if len(mounts) != 1 or mounts[0].get('Name') != name or mounts[0].get('Destination') != '/epoch':
                raise RuntimeError('Unexpected writable mount')
        return value

    def key(self, seat, epoch):
        return hmac.new(self.config['control_key'].encode(), self.identity(seat,epoch).encode(),hashlib.sha256).hexdigest()

    def model_key(self, seat, epoch):
        return hmac.new(self.config['control_key'].encode(),('model-epoch:'+self.identity(seat,epoch)).encode(),hashlib.sha256).hexdigest()

    def proxy_owned(self, seat, epoch):
        name=self.identity(seat,epoch)
        value=self.inspect('container',name+'-model')
        labels=value.get('Config',{}).get('Labels',{})
        mounts=value.get('Mounts',[])
        if (labels.get('ioalab.trial.owner')!=name or labels.get('ioalab.trial.role')!='model'
                or len(mounts)!=1 or mounts[0].get('Type')!='bind' or mounts[0].get('Source')!=str(self.secret_path)
                or mounts[0].get('Destination')!='/run/model/provider.json' or mounts[0].get('RW') is not False):
            raise RuntimeError('Model sidecar ownership or secret mount changed')
        return value

    def network_owned(self, name):
        network=self.inspect('network',name+'-net')
        if (network.get('Internal') is not True or network.get('Driver')!='bridge'
                or network.get('Labels',{}).get('ioalab.trial.owner')!=name):
            raise RuntimeError('Trial network ownership changed')
        return network

    def ui(self, seat):
        return 'http://127.0.0.1:' + str(self.config['port_base'] + seat * 2)

    def control(self, seat, epoch, method, body=None):
        self.owned('container',self.identity(seat,epoch))
        url = 'http://127.0.0.1:' + str(self.config['port_base'] + seat * 2 + 1) + '/' + method
        response = httpx.post(url,json=body or {},headers={'Authorization':'Bearer '+self.key(seat,epoch)},timeout=25,trust_env=False)
        response.raise_for_status()
        return response.json()

    def prepare(self, seat, epoch):
        name = self.identity(seat,epoch)
        if not self.secret_path.is_file() or any(p.is_symlink() for p in (self.secret_path,*self.secret_path.parents)):
            raise ValueError('Provider secret must be a dedicated regular file without symlink ancestors')
        outer=self.inspect('network',self.config['egress_network'])
        if outer.get('Driver')!='bridge' or outer.get('Internal') is not False:
            raise RuntimeError('Provider sidecar requires the reviewed external bridge')
        network_name = name + '-net'
        self.command('network','create','--internal','--label','ioalab.trial.owner='+name,network_name)
        self.network_owned(name)
        self.command('run','-d','--name',name+'-model','--label','ioalab.trial.owner='+name,
            '--label','ioalab.trial.role=model','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
            '--pids-limit','64','--memory','256m','--network',self.config['egress_network'],
            '--mount','type=bind,src='+str(self.secret_path)+',dst=/run/model/provider.json,readonly',
            '-e','IOALAB_MODEL_PROVIDER_BASE_URL='+self.config['provider_base_url'],
            '-e','IOALAB_MODEL_EPOCH_TOKEN='+self.model_key(seat,epoch),'-e','IOALAB_TRIAL_EPOCH='+epoch,
            '--entrypoint','/opt/participant/bin/python',self.config['image'],'-m','ioalab_trial.model_egress')
        self.proxy_owned(seat,epoch)
        self.command('network','connect','--alias','model-egress',network_name,name+'-model')
        # Named resource creation is deterministic; unknown partial creation does
        # not silently take over an existing container or volume.
        self.command('volume','create','--label','ioalab.trial.owner='+name,name)
        self.owned('volume',name)
        base = self.config['port_base'] + seat * 2
        self.command('run','-d','--name',name,'--label','ioalab.trial.owner='+name,
            '--read-only','--cap-drop=ALL','--cap-add=SETUID','--cap-add=SETGID','--cap-add=CHOWN','--cap-add=DAC_OVERRIDE','--security-opt=no-new-privileges',
            '--pids-limit','256','--memory',self.config.get('memory','6g'),
            '--cpus',str(self.config.get('cpus',2)),
            '--network',network_name,
            '--mount','type=volume,src='+name+',dst=/epoch',
            '-p',f'127.0.0.1:{base}:8080','-p',f'127.0.0.1:{base+1}:8767',
            '-e','IOALAB_TRIAL_EPOCH='+epoch,'-e','IOALAB_TRIAL_CONTROL_KEY='+self.key(seat,epoch),
            '-e','IOALAB_TRIAL_MODEL='+json.dumps({'name':self.config['model']['name'],
                'base_url':'http://model-egress:8081/v1','api_key':self.model_key(seat,epoch)}),
            self.config['image'])

    def healthy(self, seat, epoch):
        required = ('nativeWeb','nativeWs','a2uiEnabled','skillLoaded','sdkConnected','servicesReady','cleanInitialState','sampleBudgetAvailable')
        until = time.monotonic() + 60
        while time.monotonic() < until:
            try:
                value = self.control(seat,epoch,'health')
                if value.get('epoch') == epoch and all(value.get(key) is True for key in required):
                    return
            except (httpx.HTTPError,RuntimeError):
                pass
            time.sleep(1)
        raise RuntimeError('Real sandbox readiness evidence missing')

    def stop(self, seat, epoch):
        name = self.identity(seat,epoch)
        self.owned('container',name)
        self.proxy_owned(seat,epoch)
        # Gateway/confirmation access is already durably fenced. Model access
        # must disappear even if the seat's control listener is unavailable.
        # Terminating this epoch's only provider endpoint immediately invalidates
        # its token and interrupts outstanding model streams, before stopping work.
        self.command('kill','--signal=TERM',name+'-model')
        self.command('wait',name+'-model')
        proxy=self.proxy_owned(seat,epoch)['State']
        if proxy.get('Running') or proxy.get('Pid')!=0 or proxy.get('Status')!='exited':
            raise RuntimeError('Model sidecar execution has not released')
        self.control(seat,epoch,'revoke')
        self.command('kill','--signal=TERM',name)
        self.command('wait',name)
        if self.owned('container',name)['State']['Running']:
            raise RuntimeError('Container still running')
        # All processes in the namespace must exit; an exited main PID alone is
        # insufficient. Docker top fails for exited containers; State.Pid=0 and
        # status exited under the runtime is checked before volume removal.
        state = self.owned('container',name)['State']
        if state.get('Pid') != 0 or state.get('Status') != 'exited':
            raise RuntimeError('Namespace process ownership not released')

    def wipe(self, seat, epoch):
        name = self.identity(seat,epoch)
        state = self.owned('container',name)['State']
        if state.get('Running') or state.get('Pid') != 0:
            raise RuntimeError('Refuse destruction while writer may remain')
        self.owned('volume',name)
        proxy=self.proxy_owned(seat,epoch)['State']
        if proxy.get('Running') or proxy.get('Pid')!=0 or proxy.get('Status')!='exited':
            raise RuntimeError('Cannot destroy a live model sidecar')
        network=self.network_owned(name)
        if any(item.get('Name') not in (name,name+'-model') for item in network.get('Containers',{}).values()):
            raise RuntimeError('Trial network contains a foreign resource')
        # Validate ALL ownership before the first destructive action.
        self.command('rm',name)
        self.command('rm',name+'-model')
        self.command('volume','rm',name)
        network_name = name + '-net'
        self.command('network','rm',network_name)
        volumes = self.command('volume','ls','--filter','name=^'+name+'$','--format','{{.Name}}')
        if volumes.strip():
            raise RuntimeError('Volume destruction not confirmed')
