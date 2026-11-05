"""Fixed Docker targets. The host coordinator never mounts or reads business volumes."""
import hashlib
import ipaddress
import hmac
import json
import re
import subprocess
import time
from pathlib import Path
import httpx
import stat
from .profile import capacity, RESOURCE_LAYOUT, INGRESS_CAPABILITY, DOCKER_COMMAND


def check_secret_permissions(path):
    value=path.stat()
    if getattr(value,'st_uid',None)!=0 or getattr(value,'st_gid',None)!=0 or stat.S_IMODE(value.st_mode)!=0o600:
        raise ValueError('Provider secret must have Linux root:root ownership and mode 600')
    if re.match(r'^/mnt/[a-z](?:/|$)',str(path)):
        raise ValueError('Provider secret must be on the native Linux filesystem, not a Windows drive mount')


class DockerSandbox:
    def __init__(self, config, *, deployment_profile):
        self.config = config
        self.capacity=capacity(deployment_profile)
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
        self.image_validated=False

    def validate_image(self):
        if self.image_validated:return
        image=self.inspect('image',self.config['image'])
        labels=image.get('Config',{}).get('Labels',{}) or {}
        if (labels.get('ioalab.trial.resource_layout')!=RESOURCE_LAYOUT
                or labels.get('ioalab.trial.ingress')!=INGRESS_CAPABILITY
                or image.get('Config',{}).get('Entrypoint')!=['/opt/participant/bin/python','-m','ioalab_trial.agent']):
            raise RuntimeError('Reviewed image resource layout or ingress capability mismatch')
        self.image_validated=True

    def labels(self, name, role):
        return ['--label','ioalab.trial.owner='+name,'--label','ioalab.trial.role='+role,
                '--label','ioalab.trial.resource_layout='+RESOURCE_LAYOUT]

    @staticmethod
    def labelled(value, name, role):
        labels=(value.get('Config',{}).get('Labels',{}) if role in ('seat','model','ingress') else value.get('Labels',{})) or {}
        if any(labels.get(key)!=expected for key,expected in {
                'ioalab.trial.owner':name,'ioalab.trial.role':role,'ioalab.trial.resource_layout':RESOURCE_LAYOUT}.items()):
            raise RuntimeError('Sandbox resource ownership/role/layout mismatch')

    def identity(self, seat, epoch):
        if type(seat) is not int or seat not in range(1,self.capacity+1) or not isinstance(epoch,str) or not re.fullmatch('[a-f0-9]{32}',epoch):
            raise ValueError('Invalid sandbox ownership')
        return self.config['deployment'] + '-' + str(seat) + '-' + epoch

    def command(self, *args):
        result = self.run([*DOCKER_COMMAND,*args],capture_output=True,text=True,timeout=90)
        if result.returncode:
            raise RuntimeError('Sandbox command failed; seat remains quarantined')
        return result.stdout

    def inspect(self, kind, name):
        return json.loads(self.command(kind,'inspect',name))[0]

    def owned(self, kind, name):
        value = self.inspect(kind,name)
        self.labelled(value,name,'seat' if kind=='container' else 'data')
        if kind == 'container':
            mounts = value.get('Mounts',[])
            if (len(mounts)!=1 or mounts[0].get('Type')!='volume' or mounts[0].get('Name')!=name
                    or mounts[0].get('Destination')!='/epoch' or mounts[0].get('RW') is not True):
                raise RuntimeError('Unexpected writable mount')
        elif value.get('Driver')!='local' or value.get('Options') not in (None,{}) or value.get('Scope')!='local':
            raise RuntimeError('Unexpected trial volume driver or options')
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
        self.labelled(value,name,'model')
        if (len(mounts)!=1 or mounts[0].get('Type')!='bind' or mounts[0].get('Source')!=str(self.secret_path)
                or mounts[0].get('Destination')!='/run/model/provider.json' or mounts[0].get('RW') is not False):
            raise RuntimeError('Model sidecar ownership or secret mount changed')
        return value

    def ingress_owned(self, seat, epoch):
        name=self.identity(seat,epoch)
        value=self.inspect('container',name+'-ingress')
        self.labelled(value,name,'ingress')
        if value.get('Mounts')!=[]:raise RuntimeError('Ingress must have no mounts')
        return value

    def network_owned(self, name):
        network=self.inspect('network',name+'-net')
        self.labelled(network,name,'network')
        if (network.get('Internal') is not True or network.get('Driver')!='bridge'
                or network.get('EnableIPv6') is not False
                or network.get('Options',{}).get('com.docker.network.bridge.gateway_mode_ipv4')!='isolated'):
            raise RuntimeError('Trial network ownership changed')
        return network

    def checked_resources(self, seat, epoch):
        """One full preflight before control, the first TERM, or first removal."""
        name=self.identity(seat,epoch)
        self.validate_image()
        volume=self.owned('volume',name)
        network=self.network_owned(name)
        outer=self.inspect('network',self.config['egress_network'])
        if outer.get('Driver')!='bridge' or outer.get('Internal') is not False:
            raise RuntimeError('External transport bridge changed')
        rows={'seat':self.owned('container',name),'model':self.proxy_owned(seat,epoch),
              'ingress':self.ingress_owned(seat,epoch)}
        target_labels=rows['ingress']['Config']['Labels']
        target_address=target_labels.get('ioalab.trial.seat_address','')
        try:
            address=ipaddress.ip_address(target_address)
            subnets=[ipaddress.ip_network(item['Subnet']) for item in network.get('IPAM',{}).get('Config',[]) if item.get('Subnet')]
        except ValueError as exc:raise RuntimeError('Invalid pinned epoch target address') from exc
        if (target_labels.get('ioalab.trial.seat_id')!=rows['seat'].get('Id') or address.version!=4
                or address.is_loopback or address.is_unspecified or not address.is_private
                or not any(address in subnet for subnet in subnets)):
            raise RuntimeError('Ingress target is not the exact epoch seat')
        if rows['seat']['State'].get('Running') and rows['seat']['NetworkSettings']['Networks'][name+'-net'].get('IPAddress')!=target_address:
            raise RuntimeError('Running epoch seat address changed')
        base=self.config['port_base']+seat*2
        ports={str(port)+'/tcp':[{'HostIp':'127.0.0.1','HostPort':str(base+offset)}]
               for offset,port in enumerate((8080,8767))}
        for role,value in rows.items():
            config=value.get('Config',{});host=value.get('HostConfig',{})
            entry=['/opt/participant/bin/python','-m','ioalab_trial.agent'] if role=='seat' else ['/opt/participant/bin/python']
            command=[] if role=='seat' else ['-m','ioalab_trial.'+('model_egress' if role=='model' else 'ingress')]
            additions={'SETUID','SETGID','CHOWN','DAC_OVERRIDE'} if role=='seat' else set()
            if (config.get('Image')!=self.config['image'] or config.get('Entrypoint')!=entry
                    or (config.get('Cmd') or [])!=command
                    or config.get('User','') not in (('','0','0:0') if role!='ingress' else ('65534:65534',))
                    or host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False
                    or {value.removeprefix('CAP_') for value in host.get('CapDrop') or []}!={'ALL'}
                    or {value.removeprefix('CAP_') for value in host.get('CapAdd') or []}!=additions
                    or set(host.get('SecurityOpt') or [])!={'no-new-privileges'}
                    or host.get('Sysctls')!={'net.ipv6.conf.all.disable_ipv6':'1','net.ipv6.conf.default.disable_ipv6':'1'}
                    or host.get('PidMode','') or host.get('IpcMode') not in ('private','')
                    or host.get('Binds') or host.get('Devices') or host.get('Tmpfs')
                    or host.get('PublishAllPorts') is not False):
                raise RuntimeError('Sandbox image, entrypoint or isolation shape changed')
            expected_ports=ports if role=='ingress' else {}
            if (host.get('PortBindings') or {})!=expected_ports:
                raise RuntimeError('Epoch ingress loopback publication changed')
            if value['State'].get('Running') and role=='ingress' and value.get('NetworkSettings',{}).get('Ports')!=ports:
                raise RuntimeError('Epoch ingress actual published ports missing or replaced')
            networks=value.get('NetworkSettings',{}).get('Networks',{})
            expected={name+'-net'} if role=='seat' else {name+'-net',self.config['egress_network']}
            if set(networks)!=expected:raise RuntimeError('Sandbox network attachment changed')
            inner=networks[name+'-net']
            aliases=set(inner.get('Aliases') or [])
            allowed={name+('' if role=='seat' else '-'+role),value.get('Id',''),value.get('Id','')[:12]}
            if role=='seat':allowed.add('seat')
            if role=='model':allowed.add('model-egress')
            if (inner.get('NetworkID')!=network.get('Id') or aliases-allowed
                    or (role=='seat' and 'seat' not in aliases)
                    or (role=='model' and 'model-egress' not in aliases)
                    or any(part.get('GlobalIPv6Address') or part.get('IPv6Gateway') for part in networks.values())):
                raise RuntimeError('Epoch target alias or IPv6 shape changed')
            if role=='ingress' and host.get('ExtraHosts')!=['seat:'+target_address]:
                raise RuntimeError('Docker-pinned ingress target address changed')
            if role!='ingress' and host.get('ExtraHosts'):raise RuntimeError('Unexpected fixed host mapping')
            if role=='seat' and value['Mounts'][0].get('Source')!=volume.get('Mountpoint'):
                raise RuntimeError('Epoch writable volume source changed')
            if role=='ingress' and (host.get('Memory')!=128*1024**2 or host.get('NanoCpus')!=500000000 or host.get('PidsLimit')!=64):
                raise RuntimeError('Ingress resource constraints changed')
        expected_members={value['Id']:value['Name'].lstrip('/') for value in rows.values() if value['State'].get('Running')}
        actual_members={key:value.get('Name') for key,value in network.get('Containers',{}).items()}
        if actual_members!=expected_members:raise RuntimeError('Trial network contains a foreign or missing resource')
        return rows

    def ui(self, seat, epoch):
        rows=self.checked_resources(seat,epoch)
        if not all(rows[role]['State'].get('Running') for role in ('seat','ingress')):
            raise RuntimeError('Epoch ingress or seat is not running')
        return 'http://127.0.0.1:' + str(self.config['port_base'] + seat * 2)

    def control(self, seat, epoch, method, body=None, *, before_send=None):
        self.ui(seat,epoch)
        url = 'http://127.0.0.1:' + str(self.config['port_base'] + seat * 2 + 1) + '/' + method
        if before_send is not None:before_send()
        response = httpx.post(url,json=body or {},headers={'Authorization':'Bearer '+self.key(seat,epoch)},timeout=25,trust_env=False)
        response.raise_for_status()
        return response.json()

    def prepare(self, seat, epoch):
        name = self.identity(seat,epoch)
        if not self.secret_path.is_file() or any(p.is_symlink() for p in (self.secret_path,*self.secret_path.parents)):
            raise ValueError('Provider secret must be a dedicated regular file without symlink ancestors')
        check_secret_permissions(self.secret_path)
        self.validate_image()
        outer=self.inspect('network',self.config['egress_network'])
        if outer.get('Driver')!='bridge' or outer.get('Internal') is not False:
            raise RuntimeError('Provider sidecar requires the reviewed external bridge')
        network_name = name + '-net'
        self.command('network','create','--internal','--ipv6=false','--opt','com.docker.network.bridge.gateway_mode_ipv4=isolated',
                     *self.labels(name,'network'),network_name)
        self.network_owned(name)
        ipv6=['--sysctl','net.ipv6.conf.all.disable_ipv6=1','--sysctl','net.ipv6.conf.default.disable_ipv6=1']
        self.command('run','-d','--name',name+'-model',*self.labels(name,'model'),
            '--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',*ipv6,
            '--pids-limit','64','--memory','256m','--network',self.config['egress_network'],
            '--mount','type=bind,src='+str(self.secret_path)+',dst=/run/model/provider.json,readonly',
            '-e','IOALAB_MODEL_PROVIDER_BASE_URL='+self.config['provider_base_url'],
            '-e','IOALAB_MODEL_EPOCH_TOKEN='+self.model_key(seat,epoch),'-e','IOALAB_TRIAL_EPOCH='+epoch,
            '--entrypoint','/opt/participant/bin/python',self.config['image'],'-m','ioalab_trial.model_egress')
        self.proxy_owned(seat,epoch)
        self.command('network','connect','--alias','model-egress',network_name,name+'-model')
        # Named resource creation is deterministic; unknown partial creation does
        # not silently take over an existing container or volume.
        self.command('volume','create',*self.labels(name,'data'),name)
        self.owned('volume',name)
        base = self.config['port_base'] + seat * 2
        self.command('run','-d','--name',name,*self.labels(name,'seat'),
            '--read-only','--cap-drop=ALL','--cap-add=SETUID','--cap-add=SETGID','--cap-add=CHOWN','--cap-add=DAC_OVERRIDE','--security-opt=no-new-privileges',
            '--pids-limit','256','--memory',self.config.get('memory','6g'),
            '--cpus',str(self.config.get('cpus',2)),
            *ipv6,'--network',network_name,'--network-alias','seat',
            '--mount','type=volume,src='+name+',dst=/epoch',
            '-e','IOALAB_TRIAL_EPOCH='+epoch,'-e','IOALAB_TRIAL_CONTROL_KEY='+self.key(seat,epoch),
            '-e','IOALAB_TRIAL_MODEL='+json.dumps({'name':self.config['model']['name'],
                'base_url':'http://model-egress:8081/v1','api_key':self.model_key(seat,epoch)}),
            self.config['image'])
        seat_value=self.owned('container',name)
        seat_ip=seat_value['NetworkSettings']['Networks'][network_name]['IPAddress']
        address=ipaddress.ip_address(seat_ip)
        if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise RuntimeError('Unexpected epoch seat address')
        self.command('run','-d','--name',name+'-ingress',*self.labels(name,'ingress'),
            '--label','ioalab.trial.seat_id='+seat_value['Id'],'--label','ioalab.trial.seat_address='+seat_ip,
            '--read-only','--user','65534:65534','--cap-drop=ALL','--security-opt=no-new-privileges',*ipv6,
            '--pids-limit','64','--memory','128m','--cpus','0.5','--network',self.config['egress_network'],
            '--add-host','seat:'+seat_ip,
            '-p',f'127.0.0.1:{base}:8080','-p',f'127.0.0.1:{base+1}:8767',
            '--entrypoint','/opt/participant/bin/python',self.config['image'],'-m','ioalab_trial.ingress')
        self.command('network','connect',network_name,name+'-ingress')
        self.checked_resources(seat,epoch)

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
        self.checked_resources(seat,epoch)
        # Gateway/confirmation access is already durably fenced. Model access
        # must disappear even if the seat's control listener is unavailable.
        # Terminating this epoch's only provider endpoint immediately invalidates
        # its token and interrupts outstanding model streams, before stopping work.
        self.finish(seat,epoch,'model')
        if self.checked_resources(seat,epoch)['seat']['State'].get('Running'):
            self.control(seat,epoch,'revoke')
        self.finish(seat,epoch,'seat')
        self.finish(seat,epoch,'ingress')

    def finish(self, seat, epoch, role):
        rows=self.checked_resources(seat,epoch)
        name=self.identity(seat,epoch)+('' if role=='seat' else '-'+role)
        if rows[role]['State'].get('Running'):
            self.command('kill','--signal=TERM',name)
            self.command('wait',name)
        state=self.checked_resources(seat,epoch)[role]['State']
        if state.get('Running') or state.get('Pid')!=0 or state.get('Status')!='exited' or state.get('ExitCode')!=0:
            raise RuntimeError('Sandbox process namespace did not exit successfully')

    def wipe(self, seat, epoch):
        name = self.identity(seat,epoch)
        rows=self.checked_resources(seat,epoch)
        if any(value['State'].get('Running') or value['State'].get('Pid')!=0 or value['State'].get('Status')!='exited'
               or value['State'].get('ExitCode')!=0 for value in rows.values()):
            raise RuntimeError('Cannot destroy an unreleased sandbox')
        # Validate ALL ownership before the first destructive action.
        self.command('rm',name)
        self.command('rm',name+'-model')
        self.command('rm',name+'-ingress')
        self.command('volume','rm',name)
        network_name = name + '-net'
        self.command('network','rm',network_name)
        volumes = self.command('volume','ls','--filter','name=^'+name+'$','--format','{{.Name}}')
        if volumes.strip():
            raise RuntimeError('Volume destruction not confirmed')
        if (self.command('container','ls','-a','--filter','label=ioalab.trial.owner='+name,'--format','{{.ID}}').strip()
                or self.command('network','ls','--filter','label=ioalab.trial.owner='+name,'--format','{{.ID}}').strip()):
            raise RuntimeError('Epoch resource destruction not confirmed')
