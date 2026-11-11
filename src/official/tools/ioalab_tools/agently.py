"""Tencent's official CLI boundary; invitations never retry an uncertain send."""
import json
import os
from pathlib import Path
import subprocess
import time
import threading

from ioalab_sdk.runtime import RuntimeFault, private_text
from .calendar import address, invitation


class AgentlyNotification:
    test = False
    oauth = None

    def __init__(self, sender, command, home, *, config_dir=None):
        self.sender = address(sender)
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise RuntimeFault('invalid_input', 'Configure the installed Agently CLI command')
        self.command, self.home = command, Path(home)
        self.config_dir = str(config_dir) if config_dir else None
        self._ready_until = 0
        self._ready_lock = threading.Lock()

    def call(self, arguments, *, cwd=None):
        env = {k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
        if self.config_dir:
            env['AGENTLY_CLI_CONFIG_DIR'] = self.config_dir
        env['AGENTLY_WORKSPACE'] = 'ioalab-admin'
        # Network routing belongs to the server. No desktop proxy is injected here.
        try:
            result = subprocess.run([*self.command, *arguments], cwd=cwd, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeFault('not_ready','Agently CLI is unavailable or timed out','inspect_mail_receipt') from None
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeFault('not_ready', 'Agently CLI returned no verifiable receipt', 'inspect_mail_receipt') from None
        if result.returncode or value.get('ok') is not True:
            raise RuntimeFault('not_ready', 'Agently authorization or service is unavailable', 'inspect_mail_authorization')
        return value.get('data', {})

    def check(self):
        from datetime import datetime, timezone, timedelta
        state = self.call(['auth','status'])
        if state.get('logged_in') is not True:
            raise RuntimeFault('not_ready','Agently mailbox is not authorized','inspect_mail_authorization')
        expires = state.get('expires_at')
        if not isinstance(expires,str):
            raise RuntimeFault('not_ready','Agently credential expiry is unavailable','inspect_mail_authorization')
        try: deadline = datetime.fromisoformat(expires.replace('Z','+00:00'))
        except ValueError:
            raise RuntimeFault('not_ready','Agently credential expiry is invalid','inspect_mail_authorization') from None
        if deadline.tzinfo is None:
            raise RuntimeFault('not_ready','Agently credential expiry must include a timezone','inspect_mail_authorization')
        if deadline <= datetime.now(timezone.utc)+timedelta(minutes=5):
            self.call(['auth','refresh'])
        data = self.call(['+me'])
        # The configured sender must appear in the actual authorized alias data.
        def addresses(value):
            if isinstance(value, dict):
                return {item for part in value.values() for item in addresses(part)}
            if isinstance(value, list):
                return {item for part in value for item in addresses(part)}
            return {value.casefold()} if isinstance(value, str) and '@' in value else set()
        if self.sender.casefold() not in addresses(data):
            raise RuntimeFault('identity_conflict', 'Agently sender is outside the authorized mailbox')
        return {'status':'authenticated','sender':self.sender,'transport':'agently','messageSent':False}

    def ensure_ready(self):
        with self._ready_lock:
            if time.monotonic() >= self._ready_until:
                self.check()
                self._ready_until = time.monotonic() + 30

    def send(self, uid, title, start, end, recipient):
        recipients = [address(item) for item in recipient] if isinstance(recipient, list) else [address(recipient)]
        import hashlib
        folder = self.home / hashlib.sha256(uid.encode()).hexdigest()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder/'invite.ics'
        body = invitation(uid, title, start, end, self.sender, recipients)
        if path.exists() and path.read_text(encoding='utf-8') != body.replace('\r\n','\n'):
            raise RuntimeFault('id_conflict', 'Original invitation attachment changed')
        if not path.exists(): private_text(path, body)
        args = ['message','+send','--subject','会议邀请：'+title,'--body',
                '您收到一项会议邀请。请查看日历附件；提交邮件不表示已经接受邀请。',
                '--attachment','./invite.ics','--confirmed']
        for item in recipients: args.extend(['--to',item])
        data = self.call(args, cwd=folder)
        if data.get('queued') is not True:
            raise RuntimeFault('unknown_result','No Agently queued receipt; inspect without resending','inspect_mail_receipt')
        return {'status':'invite_submitted','calendar':'awaiting_acceptance','uid':uid,
                'recipient':recipient,'transport':'agently','delivery':'queued'}
