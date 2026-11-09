"""Authenticated browser transport for the platform configuration authority."""
import hmac
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from ..errors import LabError


def build_router(platform,require_admin,cookie):
    router=APIRouter()

    def browser(request):
        token=request.cookies.get(cookie,'')
        if not token or not platform.auth.is_admin(platform.auth.verify(token)):
            raise LabError('forbidden','Administrator browser session required',status=403)
        return token

    def authority():
        config=platform.configuration
        if config is None:raise LabError('not_ready','Platform configuration file is not loaded',status=503)
        return config

    @router.get('/api/admin/configuration')
    async def read(request:Request,operator=Depends(require_admin)):
        token=browser(request)
        return dict(authority().view(),csrf=platform.auth.configuration_csrf(token))

    @router.put('/api/admin/configuration')
    async def write(request:Request,operator=Depends(require_admin)):
        token=browser(request)
        origin=urlsplit(request.headers.get('origin',''))
        public=urlsplit(platform.s.public_url)
        if origin.scheme!=public.scheme or origin.netloc!=public.netloc or origin.netloc!=request.headers.get('host','') or origin.path not in ('','/') or origin.query or origin.fragment:
            raise LabError('forbidden','Same-origin browser request required',status=403)
        expected=platform.auth.configuration_csrf(token)
        if not hmac.compare_digest(request.headers.get('x-ioalab-csrf',''),expected):
            raise LabError('forbidden','Configuration CSRF verification failed',status=403)
        value=await request.json()
        if not isinstance(value,dict) or set(value)!={'revision','changes'}:
            raise LabError('invalid_input','Expected configuration revision and changes')
        return await platform._worker(authority().update,value['revision'],value['changes'],actor=operator)

    return router
