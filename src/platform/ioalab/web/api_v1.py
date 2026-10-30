"""Infrastructure API. Contract execution stays in the participant SDKs."""
import asyncio
import base64
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from a2c.errors import CARD_INVALID, fail
from .. import participants
from ..store import jload


def build_router(platform, current_user):
    router = APIRouter(prefix="/api/v1")

    @router.post("/token")
    def token(body: dict, request: Request):
        return platform.login(body.get('username',''),body.get('password',''),ip=request.client.host,
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
    def me(uid=Depends(current_user)):
        user = platform.db.one("SELECT id,username,display_name,kind FROM users WHERE id=?", uid)
        return {**user, "wallet": str(platform.wallets.balance(uid)),
                "roles":platform.auth.roles(uid), "identities":platform.identities.list_owned(uid)}

    def sdk_user(request:Request):
        return platform.identities.bearer(request.headers.get('authorization',''))

    @router.post('/files/{agentId}')
    async def file_upload(agentId:str,operationId:str,name:str,digest:str,request:Request,uid=Depends(sdk_user)):
        from ..files import MAX_FILE
        platform.identities.owned(uid,agentId)
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>MAX_FILE:
                from ..errors import LabError
                raise LabError('invalid_input','A protocol file may contain at most16MiB')
            chunks.append(chunk)
        return await asyncio.to_thread(platform.files.put,uid,agentId,operationId,name,digest,b''.join(chunks))

    @router.get('/files/{fileId}')
    def file_download(fileId:str,agentId:str,uid=Depends(sdk_user)):
        from urllib.parse import quote
        descriptor,data=platform.files.get(uid,agentId,fileId)
        return Response(data,media_type='application/octet-stream',headers={'ETag':'"'+descriptor['digest']+'"','Cache-Control':'private, no-store',
                        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(descriptor['name'],safe='')})

    @router.get('/services/categories')
    def service_categories(uid=Depends(sdk_user)):
        return {'categories':platform.discovery.categories()}

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
        actor = platform.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'", uid)
        return {"hits": platform._search(str(body.get("query", ""))[:2000], max(1, min(20, int(body.get("top_k", 8)))),
                                         by=actor["id"] if actor else uid), "mode": platform.discovery.last_mode}

    @router.get("/providers/reputation")
    def reputation(provider: str, uid=Depends(current_user)):
        return platform.market.reputation(provider)

    @router.get('/payments/{paymentId}')
    def payment(paymentId:str,uid=Depends(current_user)):
        return platform.psp.lookup(uid,paymentId)

    @router.get('/payments')
    def payment_by_reference(ref:str,purpose:str,period:int|None=None,uid=Depends(current_user)):
        return platform.psp.lookup(uid,ref=ref,purpose=purpose,period=period)

    @router.post("/provider/register")
    def register(body: dict, uid=Depends(current_user)):
        return platform.register_external_provider(uid, url=body["url"], name=body["name"], description=body.get("description", ""),
                tags=body.get("tags"), category=body.get("category"), ap2_jwk=body.get("ap2Jwk"))

    @router.post("/provider/refresh")
    def refresh(body: dict, uid=Depends(current_user)):
        row = platform.db.one("SELECT owner_id FROM agents WHERE id=? AND role='service'", body["id"])
        if not row or row["owner_id"] != uid:
            raise HTTPException(403, "Service does not belong to this account")
        return {"status": platform.refresh_external_provider(body["id"])}

    @router.get("/provider/mine")
    def mine(uid=Depends(current_user)):
        return platform.db.rows("SELECT id,name,status,meta FROM agents WHERE owner_id=? AND role='service'", uid)

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

    @router.post("/observations")
    def observe(body: dict, uid=Depends(current_user)):
        return participants.observe(platform, uid, body)

    @router.get("/orders/{digest}")
    def order(digest: str, uid=Depends(current_user)):
        result = platform.order_detail(digest)
        participants.party(platform, uid, result["card"])
        return result

    @router.post("/arbitrations")
    def arbitrate(body: dict, uid=Depends(current_user)):
        card, period = body["card"], int(body["period"])
        participants.party(platform, uid, card)
        files = {n: base64.b64decode(b, validate=True) for n,b in body.get("files", {}).items()}
        report = card["fulfillment"].get("periods", [])[period - 1].get("report")
        ruling = platform.market.arbitrate(card=card, period=period, files=files, report=report,
                                          applicant=uid, ground=body.get("ground", "acceptance-failed"))
        platform.sync_orders()
        return ruling

    return router
