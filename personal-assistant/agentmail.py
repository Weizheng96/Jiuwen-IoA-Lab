"""Provision an AgentMail notification inbox; credentials remain on this device."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
from configure_assistant import read_env


def private_write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w',encoding='utf-8') as stream:
        stream.write(value)
    os.replace(temp,path)


class AgentMailSetup:
    def __init__(self,home,*,http=None):
        self.path=Path(home)/'account.json'
        self.http=http or httpx.Client(base_url='https://api.agentmail.to/v0/',timeout=40,trust_env=False)

    def state(self):
        if not self.path.is_file():raise ValueError('No local AgentMail registration; run signup first')
        return json.loads(self.path.read_text(encoding='utf-8'))

    def save(self,state):private_write(self.path,json.dumps(state,indent=2)+'\n')

    def public(self,state):
        return {'inbox':state.get('inbox_id'),'owner':state.get('human_email'),'verified':state.get('verified',False),'status':state['status'],'stateFile':str(self.path)}

    def request(self,path,body,*,key=None):
        response=self.http.post(path,json=body,headers={'Authorization':'Bearer '+key} if key else {})
        if not response.is_success:
            try:code=response.json().get('code','request_failed')
            except ValueError:code='request_failed'
            if not isinstance(code,str) or not re.fullmatch('[a-z_]{1,80}',code):code='request_failed'
            raise RuntimeError(f'AgentMail HTTP {response.status_code}: {code}; no automatic retry')
        return response.json()

    def signup(self,human_email,username):
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+',human_email):raise ValueError('A valid owner email is required')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}',username):raise ValueError('Invalid inbox username')
        if self.path.exists():
            state=self.state()
            if state['human_email']!=human_email or state['username']!=username:raise ValueError('Existing registration belongs to different parameters; preserve it')
            if not state.get('api_key'):raise RuntimeError('Previous signup result is unknown or rejected; inspect the Console before trying again')
            return self.public(state)
        state={'human_email':human_email,'username':username,'status':'signup_pending','verified':False}
        # Persist intent before the request. A timeout must not cause key rotation.
        self.save(state)
        response=self.request('agent/sign-up',{'human_email':human_email,'username':username,'source':'jiuwen-ioa-lab'})
        if not all(isinstance(response.get(k),str) and response[k] for k in ('api_key','inbox_id','organization_id')):raise ValueError('Unexpected AgentMail signup response; preserve local pending state')
        state.update({k:response[k] for k in ('api_key','inbox_id','organization_id')},status='verification_pending')
        self.save(state)
        return self.public(state)

    def verify(self,code):
        if not re.fullmatch(r'\d{6}',code):raise ValueError('Enter the six-digit AgentMail ownership code')
        state=self.state()
        if state.get('verified'):return self.public(state)
        if not state.get('api_key'):raise ValueError('Signup has no usable local key')
        response=self.request('agent/verify',{'otp_code':code},key=state['api_key'])
        if response.get('verified') is not True:raise RuntimeError('AgentMail did not confirm ownership verification')
        state.update(verified=True,status='verified');self.save(state)
        return self.public(state)

    def configure(self,env_path):
        state=self.state()
        if not state.get('verified'):raise ValueError('Verify AgentMail ownership before enabling external delivery')
        inbox=state['inbox_id']
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+',inbox):raise ValueError('AgentMail inbox is not an email address')
        path=Path(env_path)
        values=read_env(path if path.exists() else ROOT/'deploy/personal-assistant.env.example')
        values.update(IOALAB_SMTP_AUTH='password',IOALAB_SMTP_HOST='smtp.agentmail.to',IOALAB_SMTP_PORT='465',IOALAB_SMTP_FROM=inbox,IOALAB_SMTP_USERNAME=inbox,IOALAB_SMTP_PASSWORD=state['api_key'])
        private_write(path,'\n'.join(k+'='+json.dumps(v,ensure_ascii=False) for k,v in values.items())+'\n')
        return {'inbox':inbox,'configured':str(path),'smtpHost':'smtp.agentmail.to','smtpPort':465}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,default=ROOT/'data/agentmail')
    sub=parser.add_subparsers(dest='command',required=True)
    signup=sub.add_parser('signup');signup.add_argument('--human-email',required=True);signup.add_argument('--username',default='jiuwenioa')
    verify=sub.add_parser('verify');verify.add_argument('--code-file',type=Path,help='UTF-8 file containing only the six-digit ownership code')
    config=sub.add_parser('configure');config.add_argument('--env',type=Path,default=ROOT/'data/personal-assistant.env')
    sub.add_parser('status');args=parser.parse_args();setup=AgentMailSetup(args.home)
    try:
        if args.command=='signup':result=setup.signup(args.human_email,args.username)
        elif args.command=='verify':
            if args.code_file:code=args.code_file.read_text(encoding='utf-8-sig').strip()
            else:
                import getpass
                code=getpass.getpass('AgentMail ownership code: ').strip()
            result=setup.verify(code)
        elif args.command=='configure':result=setup.configure(args.env)
        else:result=setup.public(setup.state())
        print(json.dumps(result,ensure_ascii=False))
    except (httpx.HTTPError,ValueError,RuntimeError) as exc:
        # Never include a provider response body or credentials in output.
        if isinstance(exc,httpx.HTTPError):message='AgentMail network request failed; retain local state and inspect before retrying'
        else:message=str(exc)
        print(json.dumps({'error':message},ensure_ascii=False));raise SystemExit(1)
    finally:setup.http.close()


if __name__=='__main__':main()
