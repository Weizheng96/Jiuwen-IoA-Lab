"""Issue admission only; trial queue and business identities live outside the core."""
import base64
import hashlib
import hmac
import json
import ipaddress
import secrets
import time
from urllib.parse import urlsplit

from ..errors import LabError


def admission_origin(url):
    """Canonical origin comparison; browser cookies still require separate names."""
    try:
        parts=urlsplit(url)
        local=parts.hostname in ('127.0.0.1','localhost','::1')
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ('','/') or parts.scheme not in (('http','https') if local else ('https',))):
            raise ValueError('Invalid admission origin')
        port=parts.port if parts.port is not None else 443 if parts.scheme=='https' else 80
        if port<1:raise ValueError('Invalid admission port')
        return parts.scheme,parts.hostname.lower(),port
    except ValueError:
        raise LabError('not_ready','在线试用尚未配置可用入口',status=503) from None


def entry(settings, owner, *, clock=time.time, confirmation=False):
    url = settings.trial_confirm_url if confirmation else settings.trial_url
    key = settings.trial_admission_key
    selected=admission_origin(url)
    if len(key.encode()) < 32:
        raise LabError('not_ready', '在线试用尚未配置可用入口', status=503)
    formal=admission_origin(settings.public_url)
    if selected==formal:
        raise LabError('not_ready','试用入口必须与正式平台使用独立Origin',status=503)
    if confirmation:
        native=admission_origin(settings.trial_url)
        if selected==native or native==formal:
            raise LabError('not_ready','可信确认必须使用独立Origin',status=503)
        if native[1]==selected[1]:
            try:ipaddress.ip_address(native[1])
            except ValueError:
                raise LabError('not_ready','共享网站名仅允许显式IP与不同HTTPS端口',status=503) from None
            if native[0]!='https' or selected[0]!='https':
                raise LabError('not_ready','同IP试用必须使用不同HTTPS端口',status=503)
    now = int(clock())
    body = {'v': 1, 'owner': owner, 'aud': url, 'iat': now, 'exp': now + 60,
            'jti': secrets.token_urlsafe(24)}
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {'url': url + '/entry#' + payload + '.' + signature, 'expiresAt': body['exp']}
