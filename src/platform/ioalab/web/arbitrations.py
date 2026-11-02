"""Independent web administrators read only submitted evidence of the original case."""
from urllib.parse import quote
from fastapi import APIRouter,Depends
from fastapi.responses import Response


def build_router(platform,require_admin):
    router=APIRouter(prefix='/api/admin/arbitrations')

    @router.get('')
    async def cases(state:str|None=None,limit:int=50,cursor:str|None=None,operator=Depends(require_admin)):
        return await platform._worker(platform.arbitration_cases.admin_list,operator,state=state,limit=limit,cursor=cursor)

    @router.get('/{caseRef}')
    async def case(caseRef:str,operator=Depends(require_admin)):
        return await platform._worker(platform.arbitration_cases.admin_get,operator,caseRef)

    @router.get('/{caseRef}/materials/{fileId}')
    async def material(caseRef:str,fileId:str,operator=Depends(require_admin)):
        descriptor,data=await platform._worker(platform.arbitration_cases.material,caseRef,fileId,operator=operator)
        return Response(data,media_type='application/octet-stream',headers={
            'ETag':'"'+descriptor['digest']+'"','Cache-Control':'private, no-store',
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(descriptor['name'],safe='')})

    @router.post('/{caseRef}/review')
    async def review(caseRef:str,body:dict,operator=Depends(require_admin)):
        return await platform._worker(platform.arbitration_cases.admin_review,operator,caseRef,body)

    @router.post('/{caseRef}/reevaluate')
    async def reevaluate(caseRef:str,body:dict,operator=Depends(require_admin)):
        return await platform._worker(platform.arbitration_cases.admin_reevaluate,operator,caseRef,body)

    return router
