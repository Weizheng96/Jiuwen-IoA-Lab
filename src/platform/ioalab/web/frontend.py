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
        names={}
        for agent in platform.db.rows("SELECT owner_id,name FROM agents WHERE role='assistant' AND status<>'disabled' ORDER BY created,id"):
            names.setdefault(agent['owner_id'],[]).append(agent['name'])
        return [{'username':user['username'],'displayName':user['display_name'],'assistantNames':names.get(user['id'],[])}
                for user in platform.db.rows('SELECT id,username,display_name FROM users ORDER BY created,id')]

    @router.get('/api/people/{username}/history')
    def history(username:str,uid=Depends(current_user)):
        peer=platform.db.one('SELECT id FROM users WHERE username=?',username)
        if not peer:raise HTTPException(404,'用户不存在')
        return {'messages':[platform.message_views.view(row,private=True) for row in platform.message_views.rows(uid,peer=peer['id'])],
                'intents':platform.contract_views.intents(uid,peer=peer['id']),
                'contracts':[platform.contract_views.view(row,private=True) for row in platform.contract_views.rows(uid,peer=peer['id'])]}

    @router.get('/api/public/stats')
    def statistics():
        return {'services':len(platform.service_views.list()),
                'assistants':platform.db.one("SELECT COUNT(*) AS n FROM agents WHERE role='assistant' AND status<>'disabled'")['n'],
                'interactions':platform.db.one('SELECT COUNT(*) AS n FROM delivery_interactions')['n']}

    return router
