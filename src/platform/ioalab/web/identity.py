"""Public identity metadata, distinct from publishing or executing a service."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from ..errors import LabError


def public_service_app(enrollments):
    async def keys(request):
        try:
            return JSONResponse(enrollments.service_keys(request.path_params['agentId']))
        except LabError as exc:
            return JSONResponse({'error':exc.public()},status_code=exc.status)

    async def unavailable(request):
        return JSONResponse({'error':{'code':'not_ready','message':'Service identity is bound; business publication and admission are not ready','recovery':'configure_service'}},status_code=409)

    return Starlette(routes=[Route('/{agentId}/.well-known/jwks.json',keys),
                             Route('/{agentId}/.well-known/agent-card.json',unavailable),
                             Route('/{agentId}',unavailable,methods=['POST'])])
