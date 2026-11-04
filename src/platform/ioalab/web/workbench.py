"""Authorized browser views; reading never consumes participant messages or signs a card."""
from fastapi import APIRouter,Depends,HTTPException



def build_router(platform,current_user,require_admin,require_auditor):
    router=APIRouter()

    @router.get('/api/workbench')
    def summary(uid=Depends(current_user)):
        return platform.workbench_views.summary(uid)

    @router.get('/api/catalog')
    def catalog(uid=Depends(current_user)):return platform.service_views.list()

    @router.get('/api/services/{sid}/orders')
    def orders(sid:str,uid=Depends(current_user)):
        service=platform.identities.owned(uid,sid)
        if service['role']!='service':raise HTTPException(403,'请选择本账号服务')
        return platform.workbench_views.service_orders(uid,sid)

    @router.get('/api/admin/messages')
    def all_messages(operator=Depends(require_admin)):
        return [{**row,'canAudit':platform.auth.is_auditor(operator)} for row in platform.message_views.list()]

    @router.get('/api/admin/messages/{mid}')
    def audit(mid:str,operator=Depends(require_auditor)):
        row=platform.message_views.detail(mid)
        if not row:raise HTTPException(404,'消息不可用')
        platform.bus.emit('message','message.audit',sender=operator,
            detail={'messageId':mid,'interactionId':row['interactionId'],'source':'web_audit'})
        return row

    return router
