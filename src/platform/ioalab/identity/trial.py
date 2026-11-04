"""Issue admission only; trial queue and business identities live outside the core."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

from ..errors import LabError


def entry(settings, owner, *, clock=time.time, confirmation=False):
    url = settings.trial_confirm_url if confirmation else settings.trial_url
    key = settings.trial_admission_key
    parts = urlsplit(url)
    local = parts.hostname in ('127.0.0.1', 'localhost', '::1')
    if (len(key.encode()) < 32 or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/')
            or parts.scheme not in (('http', 'https') if local else ('https',))):
        raise LabError('not_ready', '在线试用尚未配置可用入口', status=503)
    if confirmation and (not settings.trial_url or urlsplit(settings.trial_url).hostname == parts.hostname):
        raise LabError('not_ready', '可信确认必须使用独立的网站地址', status=503)
    now = int(clock())
    body = {'v': 1, 'owner': owner, 'aud': url, 'iat': now, 'exp': now + 60,
            'jti': secrets.token_urlsafe(24)}
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {'url': url + '/entry#' + payload + '.' + signature, 'expiresAt': body['exp']}
