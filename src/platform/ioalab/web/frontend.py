"""Public presentation views and browser entry points; never participant execution."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

STATIC=Path(__file__).parent/'static'


def build_router(platform,subject,current_user):
    router=APIRouter()

    @router.get('/services')
    def services_page():return FileResponse(STATIC/'providers.html')

    @router.get('/assistants')
    def assistants_page():return FileResponse(STATIC/'assistants.html')

    @router.get('/connect')
    def connect_page():return FileResponse(STATIC/'connect.html')

    @router.get('/login')
    @router.get('/register')
    def account_page():return FileResponse(STATIC/'auth.html')

    @router.get('/workbench')
    def workbench_page(request:Request):
        actor=subject(request)
        if actor is None:return RedirectResponse('/login?'+urlencode({'next':'/workbench'}),status_code=303)
        if actor=='admin':return RedirectResponse('/admin',status_code=303)
        return FileResponse(STATIC/'workbench.html')

    @router.get('/api/public/catalog')
    def catalog():return platform.service_views.list()

    @router.get('/api/public/categories')
    def categories():return [{key:item.get(key,'') for key in ('id','name','description')} for item in platform.discovery.categories()]

    @router.get('/api/public/assistants')
    def people():
        return platform.account_views.people()

    @router.get('/api/people/{username}/history')
    def history(username:str,uid=Depends(current_user)):
        return platform.account_views.history(uid,username)

    @router.get('/api/public/stats')
    def statistics():
        return platform.account_views.statistics()

    return router
