"""Public, read-only core identity probe usable from installed service units."""
import argparse
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def check_ready(url,expected_public_url,*,request_timeout=3):
    for value in (url,expected_public_url):
        parsed=urlsplit(value)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('','/') or parsed.query or parsed.fragment):
            raise ValueError('Use fixed HTTP(S) root origins')
        parsed.port
    with build_opener(ProxyHandler({}),NoRedirect()).open(url.rstrip('/')+'/api/config',timeout=request_timeout) as reply:
        value=json.load(reply)
    if not isinstance(value,dict) or value.get('publicUrl')!=expected_public_url:
        raise ValueError('The responding core has a different public identity')


def wait_ready(url,expected_public_url,*,timeout=180,request_timeout=3):
    if (not math.isfinite(timeout) or not 0<=timeout<=600 or not math.isfinite(request_timeout)
            or not 0<request_timeout<=10):raise ValueError('Use bounded readiness timeouts')
    deadline=time.monotonic()+timeout
    while True:
        try:
            check_ready(url,expected_public_url,request_timeout=request_timeout if timeout==0 else min(request_timeout,max(.01,deadline-time.monotonic())))
            return
        except HTTPError as error:
            if error.code<500:raise ValueError('The readiness endpoint rejected the public probe') from None
        except (OSError,URLError):pass
        if time.monotonic()>=deadline:raise RuntimeError('Core readiness was not verified before the deadline')
        time.sleep(min(.2,max(0,deadline-time.monotonic())))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--expected-public-url',required=True)
    parser.add_argument('--timeout',type=float,default=180)
    parser.add_argument('--request-timeout',type=float,default=3)
    args=parser.parse_args(argv)
    try:wait_ready(args.url,args.expected_public_url,timeout=args.timeout,request_timeout=args.request_timeout)
    except (ValueError,RuntimeError):
        print(json.dumps({'status':'rejected','ready':False,'error':'core_readiness_unverified'}));return 1
    print(json.dumps({'status':'completed','ready':True}));return 0


if __name__=='__main__':raise SystemExit(main())
