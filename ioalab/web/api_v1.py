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
        user = platform.login(body.get("username", ""), body.get("password", ""), ip=request.client.host)
        return {**user, "token": platform.auth.bearer(user["id"])}

    @router.get("/me")
    def me(uid=Depends(current_user)):
        user = platform.db.one("SELECT id,username,display_name,kind FROM users WHERE id=?", uid)
        return {**user, "wallet": str(platform.wallets.balance(uid)),
                "identities": platform.db.rows("SELECT id,role,name,status FROM agents WHERE owner_id=?", uid)}

    @router.get("/identity/challenge")
    def challenge(uid=Depends(current_user)):
        return participants.challenge(platform, uid)

    @router.post("/identity")
    def identity(body: dict, uid=Depends(current_user)):
        return participants.bind(platform, uid, body)

    @router.post("/search")
    def search(body: dict, uid=Depends(current_user)):
        actor = platform.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'", uid)
        return {"hits": platform._search(str(body.get("query", ""))[:2000], max(1, min(20, int(body.get("top_k", 8)))),
                                         by=actor["id"] if actor else uid), "mode": platform.discovery.last_mode}

    @router.get("/providers/reputation")
    def reputation(provider: str, uid=Depends(current_user)):
        return platform.market.reputation(provider)

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
        return platform.packages.put(verifier_id=body["verifierId"], source=source, reads=body.get("reads", []))

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
