"""Infrastructure API. Contract execution stays in the participant SDKs."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response


def build_router(platform, current_user):
    router = APIRouter(prefix="/api/v1")

    def sdk_user(request:Request):
        return platform.identities.bearer(request.headers.get('authorization',''))

    @router.post("/token")
    def token(body: dict, request: Request):
        from .source import client_ip
        return platform.accounts.login(body.get('username',''),body.get('password',''),ip=client_ip(request,platform.s),
                              operation_id=body.get('operationId'),audience='sdk')

    @router.post('/token/refresh')
    def refresh(request:Request):
        uid=platform.identities.bearer(request.headers.get('authorization',''))
        from datetime import datetime,timezone
        import time
        until=int(time.time())+30*86400
        return {'id':uid,'token':platform.auth.token('assistant:'+uid,expires_at=until),
                'expiresAt':datetime.fromtimestamp(until,timezone.utc).isoformat().replace('+00:00','Z'),'credited':False}

    @router.get("/me")
    def me(uid=Depends(sdk_user)):
        return platform.account_views.sdk_me(uid)


    @router.post('/files/{agentId}')
    async def file_upload(agentId:str,operationId:str,name:str,digest:str,request:Request,uid=Depends(sdk_user)):
        platform.identities.owned(uid,agentId)
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            platform.files.check_size(size)
            chunks.append(chunk)
        return await platform._worker(platform.files.put,uid,agentId,operationId,name,digest,b''.join(chunks))

    @router.get('/files/{fileId}')
    def file_download(fileId:str,agentId:str,uid=Depends(sdk_user)):
        from urllib.parse import quote
        descriptor,data=platform.files.get(uid,agentId,fileId)
        return Response(data,media_type='application/octet-stream',headers={'ETag':'"'+descriptor['digest']+'"','Cache-Control':'private, no-store',
                        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(descriptor['name'],safe='')})

    @router.get('/services/categories')
    def service_categories(uid=Depends(sdk_user)):
        return {'categories':platform.discovery.categories()}

    @router.post('/services/category-preview')
    def category_preview(body:dict,uid=Depends(sdk_user)):
        return platform.category_preview.preview(body)

    @router.post('/services/{agentId}/publications')
    def service_publish(agentId:str,body:dict,uid=Depends(sdk_user)):
        return platform.service_operations.publish(uid,agentId,body)

    @router.get('/services/{agentId}/status')
    def service_status(agentId:str,uid=Depends(sdk_user)):
        return platform.publications.status(uid,agentId)

    @router.post('/identity/challenge')
    def challenge(body:dict,uid=Depends(sdk_user)):
        return platform.identities.enrollment.issue(uid,body)

    @router.post("/identity")
    def identity(body:dict,uid=Depends(sdk_user)):
        return platform.accounts.bind_identity(platform.identities.enrollment,uid,body)

    @router.post("/search")
    def search(body: dict, uid=Depends(current_user)):
        return {"hits": platform.discovery_operations.search(str(body.get("query", ""))[:2000], max(1, min(20, int(body.get("top_k", 8)))),
                                         by=platform.identities.search_actor(uid)), "mode": platform.discovery.last_mode}

    @router.get("/providers/reputation")
    def reputation(provider: str, uid=Depends(current_user)):
        return platform.market.reputation(provider)

    @router.get('/payments/{paymentId}')
    def payment(paymentId:str,uid=Depends(current_user)):
        return platform.psp.lookup(uid,paymentId)

    @router.get('/payments')
    def payment_by_reference(ref:str,purpose:str,period:int|None=None,uid=Depends(current_user)):
        return platform.psp.lookup(uid,ref=ref,purpose=purpose,period=period)



    @router.get("/provider/mine")
    def mine(uid=Depends(current_user)):
        return platform.account_views.services_of(uid)

    @router.post("/provider/packages")
    def packages(body: dict, uid=Depends(current_user)):
        source = body.get("source", "")
        if not isinstance(source, str) or len(source.encode()) > 256 * 1024:
            raise HTTPException(400, "Package source exceeds the limit")
        return platform.packages.put(verifier_id=body["verifierId"], source=source, reads=body.get("reads", []), kind=body.get("kind","script"))

    @router.get("/packages/{digest}")
    def package(digest: str):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise HTTPException(400, "Invalid package digest")
        return Response(platform.packages.fetch("ioalab://pkg/" + digest), media_type="application/zip")



    @router.get('/arbitrations')
    async def case_by_admission(agentId:str,admissionId:str,uid=Depends(current_user)):
        return await platform._worker(platform.arbitration_cases.by_admission,uid,agentId,admissionId)

    @router.get('/arbitrations/{caseRef}')
    async def case_status(caseRef:str,agentId:str,uid=Depends(current_user)):
        return await platform._worker(platform.arbitration_cases.get,uid,agentId,caseRef)

    @router.get('/arbitrations/{caseRef}/materials/{fileId}')
    async def case_material(caseRef:str,fileId:str,agentId:str,uid=Depends(current_user)):
        from urllib.parse import quote
        descriptor,data=await platform._worker(platform.arbitration_cases.material,caseRef,fileId,owner=uid,actor=agentId)
        return Response(data,media_type='application/octet-stream',headers={'ETag':'"'+descriptor['digest']+'"',
            'Cache-Control':'private, no-store','Content-Disposition':"attachment; filename*=UTF-8''"+quote(descriptor['name'],safe='')})

    @router.post('/arbitrations/preview')
    async def dispute_preview(body:dict,uid=Depends(sdk_user)):
        return await platform._worker(platform.arbitration_evidence.preview,uid,body)


    return router
