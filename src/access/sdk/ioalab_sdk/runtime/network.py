"""One instance's HTTP pool and system trust policy; credentials are request scoped."""
from http.cookiejar import CookieJar, DefaultCookiePolicy
import ssl
from urllib.parse import urlsplit
import httpx
from .protocol import RuntimeFault


class NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):return False


class Network:
    def __init__(self, base):
        self.base=base.rstrip('/')
        self.ssl=ssl.create_default_context()
        self.http=httpx.Client(timeout=120,trust_env=False,verify=self.ssl,
                               follow_redirects=False,cookies=CookieJar(policy=NoCookies()))

    def public_url(self, url):
        value=urlsplit(url);base=urlsplit(self.base)
        def origin(parsed):return (parsed.scheme,parsed.hostname,parsed.port or {'https':443,'http':80}.get(parsed.scheme))
        if origin(value)!=origin(base) or value.username is not None or value.password is not None or value.fragment:
            raise RuntimeFault('forbidden','Public protocol target is outside this platform')
        return url

    def close(self):self.http.close()

    def __enter__(self):return self

    def __exit__(self,*_):self.close()
