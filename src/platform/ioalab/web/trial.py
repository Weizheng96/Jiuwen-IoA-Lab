"""Authenticated trial entry; no participant execution or trial dependency."""
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..identity import trial as admission
from .cookies import session_cookie_name


def build_router(platform):
    router = APIRouter()
    cookie = session_cookie_name(platform.s.public_url)

    def owner(request):
        actor = platform.auth.verify(request.cookies.get(cookie))
        if not actor or actor == 'admin' or actor.startswith('assistant:'):
            raise HTTPException(401, '请使用网页登录账号进入试用')
        if not platform.account_views.exists(actor):
            raise HTTPException(401, '账号不可用')
        return actor

    @router.get('/trial')
    def page(request: Request):
        try:
            owner(request)
        except HTTPException:
            return RedirectResponse('/login?next=/trial', status_code=303)
        return FileResponse(Path(__file__).parent / 'static' / 'trial.html', headers={'Cache-Control': 'no-store'})

    @router.post('/api/trial/entry')
    def issue(request: Request):
        return issue_entry(request)

    @router.post('/api/trial/confirmation-entry')
    def issue_confirmation(request: Request):
        return issue_entry(request, confirmation=True)

    def issue_entry(request, confirmation=False):
        # Cookie possession alone is insufficient for cross-origin admission.
        origin = request.headers.get('origin', '')
        if (not origin or urlsplit(origin).netloc != request.headers.get('host')
                or urlsplit(origin).scheme != urlsplit(platform.s.public_url).scheme):
            raise HTTPException(403, '请从当前平台页面进入试用')
        return admission.entry(platform.s, owner(request), confirmation=confirmation)

    return router
