"""Public identity metadata, distinct from publishing or executing a service."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.routing import Mount
from a2c.binding import build_app
from a2c.common import Endpoint,Reply
from ..errors import LabError


def public_service_app(enrollments,publications):
    async def keys(request):
        try:
            return JSONResponse(enrollments.service_keys(request.path_params['agentId']))
        except LabError as exc:
            return JSONResponse({'error':exc.public()},status_code=exc.status)

    async def unavailable(request):
        return JSONResponse({'error':{'code':'not_ready','message':'Business admission is not ready; published templates use the advertised A2A RPC interface','recovery':'configure_service'}},status_code=409)

    async def metadata(request):
        try:
            actor=request.path_params['agentId']
            value=publications.card(actor) if request.url.path.endswith('agent-card.json') else publications.public(actor)
            return JSONResponse(value)
        except LabError as exc:return JSONResponse({'error':exc.public()},status_code=exc.status)

    class Templates(Endpoint):
        OPS=('template.get',)

        def __init__(self,actor):
            super().__init__()
            self.actor=actor

        def op_template_get(self,body,files,task_id):
            from a2c.errors import fail,UNKNOWN_CARD
            try:templates=publications.templates(self.actor,body.get('templateDigest'))
            except LabError:fail(UNKNOWN_CARD,'Template was not published by this service')
            return Reply({'op':'template.get','templates':templates})

    class Protocol:
        def __init__(self):
            # The upstream handler owns live task queues. Keep one application
            # per service rather than finalizing task coroutines on worker GC.
            self.apps={}

        async def __call__(self,scope,receive,send):
            try:
                actor=scope['path_params']['agentId']
                declaration=publications.public(actor)['declaration']
                if actor not in self.apps:
                    self.apps[actor]=build_app(Templates(actor),name=declaration['name'],base_url=declaration['protocolId'],
                                              role='provider',jwks=enrollments.service_keys(actor))
                app=self.apps[actor]
                await app(scope,receive,send)
            except LabError as exc:
                await JSONResponse({'error':exc.public()},status_code=exc.status)(scope,receive,send)

    return Starlette(routes=[Route('/{agentId}/.well-known/jwks.json',keys),
                             Route('/{agentId}/.well-known/agent-card.json',metadata),
                             Route('/{agentId}/publication',metadata),
                             Route('/{agentId}',unavailable,methods=['POST']),
                             Mount('/{agentId}',app=Protocol())])
