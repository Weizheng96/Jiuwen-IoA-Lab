"""A2C's public A2A adapter with rehydration of participant-owned task state."""
from a2c.binding import (A2CExecutor,A2CRequestHandler,EndpointTaskStore,agent_card,to_parts,
                         A2C_EXTENSION,AP2_EXTENSION,CARD_PATH,JWKS_PATH,RPC_PATH)
from a2a.server.routes import create_agent_card_routes,create_jsonrpc_routes
from a2a.types import Task,TaskStatus,TaskState
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


class RecoverableTaskStore(EndpointTaskStore):
    def __init__(self,endpoint):
        super().__init__(endpoint)
        self.recovered=set(endpoint.tasks)

    async def get(self,task_id,context=None):
        task=await super().get(task_id,context)
        if task is None and task_id in self.recovered:
            # The protocol task is durable, but the A2A envelope cache is process-local.
            task=Task(id=task_id,context_id=task_id,status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
            await self.save(task,context)
            task=await super().get(task_id,context)
        return task


def build_provider_app(provider,*,name,base_url):
    card=agent_card(name=name,base_url=base_url,role='provider',extensions=(A2C_EXTENSION,AP2_EXTENSION))
    handler=A2CRequestHandler(agent_executor=A2CExecutor(provider,(A2C_EXTENSION,AP2_EXTENSION)),
                             task_store=RecoverableTaskStore(provider),agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card,card_url=CARD_PATH),
                             *create_jsonrpc_routes(handler,RPC_PATH),
                             Route(JWKS_PATH,lambda request:JSONResponse({'keys':[provider.signer.jwk]}))])
