"""Thin HTTP recovery and authenticated participant WebSocket routes."""
import asyncio
import json
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,Request,WebSocket,WebSocketDisconnect
from ..errors import LabError


def build_router(platform,current_user):
    router=APIRouter()
    directory,broker=platform.identities,platform.messaging.delivery

    def sdk_user(request:Request):
        return directory.bearer(request.headers.get('authorization',''))

    @router.post('/api/v1/connections/ticket')
    def ticket(body:dict,uid=Depends(sdk_user)):
        return directory.issue(uid,body.get('agentIds'))

    @router.post('/api/v1/messaging/interfaces')
    def publish(body:dict,uid=Depends(sdk_user)):
        payload={k:v for k,v in body.items() if k!='operationId'}
        return broker.publish(uid,body.get('operationId'),payload)

    @router.get('/api/v1/interactions')
    def interaction(agentId:str,originMessageId:str|None=None,interactionId:str|None=None,uid=Depends(current_user)):
        if bool(originMessageId)==bool(interactionId):raise LabError('invalid_input','Specify one interaction or origin')
        return broker.interaction(uid,agentId,origin=originMessageId,iid=interactionId)

    @router.get('/api/v1/messages/{messageId}')
    def message(messageId:str,agentId:str,uid=Depends(current_user)):
        return broker.message(uid,agentId,messageId)

    @router.get('/api/v1/operations/{operationId}')
    def operation(operationId:str,agentId:str,operation:str,uid=Depends(current_user)):
        return broker.operation(uid,agentId,operation,operationId)

    @router.websocket('/ws/agents')
    async def agents(websocket:WebSocket):
        origin=websocket.headers.get('origin')
        if websocket.scope.get('query_string') or (origin and urlsplit(origin).netloc!=websocket.headers.get('host')):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection=str(uuid.uuid4())
        tasks=[]
        send_lock=asyncio.Lock()

        async def send(value):
            async with send_lock:await websocket.send_json(value)

        async def read():
            event=await websocket.receive()
            if event['type']=='websocket.disconnect':raise WebSocketDisconnect(event.get('code',1000))
            raw=event.get('text')
            if not isinstance(raw,str):raise LabError('invalid_input','Control frames must be JSON text')
            if len(raw.encode())>131072:raise LabError('invalid_input','Control frame exceeds 128 KiB')
            try:return json.loads(raw)
            except (ValueError,RecursionError) as exc:raise LabError('invalid_input','Invalid JSON control frame') from exc

        try:
            frame=await asyncio.wait_for(read(),timeout=5)
            if not isinstance(frame,dict) or frame.get('type')!='connection.auth':raise LabError('forbidden','First frame must authenticate',status=401)
            uid,actors=await asyncio.to_thread(directory.consume,frame.get('ticket'))
            ready=await asyncio.to_thread(broker.connect,uid,actors,frame.get('runtimeId'),frame.get('cursors',{}),connection)
            generations=ready['generations']
            await send({'type':'connection.ready',**ready})
            event=asyncio.Event()
            broker.listen(connection,asyncio.get_running_loop(),event)
            sent={actor:frame.get('cursors',{}).get(actor,0) for actor in actors}

            async def receive():
                while True:
                    request=await read()
                    if not isinstance(request,dict) or len(json.dumps(request).encode())>131072:
                        raise LabError('invalid_input','Invalid control frame')
                    if request.get('type')=='connection.ping':
                        await asyncio.to_thread(broker.heartbeat,uid,actors,connection,generations)
                        await send({'type':'connection.pong','connectionId':connection})
                        continue
                    try:
                        result=await asyncio.to_thread(broker.control,uid,connection,generations,request)
                        await send({'type':'operation.result','agentId':request.get('agentId'),'operationId':request.get('operationId'),
                                    'operation':request.get('type'),'ok':True,'result':result})
                    except LabError as exc:
                        await send({'type':'operation.result','agentId':request.get('agentId'),'operationId':request.get('operationId'),
                                    'operation':request.get('type'),'ok':False,'error':exc.public()})
                        if exc.code=='stale_generation':raise

            async def deliver():
                while True:
                    event.clear()
                    await asyncio.to_thread(broker.cleanup)
                    more=False
                    for actor in actors:
                        batch=await asyncio.to_thread(broker.deliveries,uid,actor,connection,generations[actor],sent[actor])
                        for value in batch:
                            await send(value)
                            sent[actor]=value['recipientSeq']
                        more=more or len(batch)==100
                    if more:continue
                    try:await asyncio.wait_for(event.wait(),timeout=platform.s.ws_heartbeat_seconds)
                    except asyncio.TimeoutError:pass

            tasks=[asyncio.create_task(receive()),asyncio.create_task(deliver())]
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        except LabError as exc:
            try:
                await send({'type':'connection.error','error':exc.public()})
                await websocket.close(code=4409 if exc.code=='stale_generation' else 4401 if exc.status==401 else 4403)
            except (RuntimeError,WebSocketDisconnect):pass
        except (WebSocketDisconnect,asyncio.TimeoutError):
            pass
        finally:
            for task in tasks:task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
            await asyncio.to_thread(broker.disconnect,connection)
    return router
