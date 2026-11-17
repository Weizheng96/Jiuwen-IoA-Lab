"""Owned native Runtime worker. No SDK imports, credentials, or extra listener."""
import asyncio
from dataclasses import asdict
import secrets

from jiuwenswarm.runtime.session_provisioner import SessionCreateInput


def snapshot_wire(snapshot):
    result=asdict(snapshot)
    for key in ('state','work_kind','root_work_kind'):
        value=result.get(key)
        if value is not None:result[key]=value.value
    return result


class NativeWorker:
    def __init__(self,runtime,source,entry_ref,post,prepare_turn=None):
        self.runtime=runtime;self.source=dict(source);self.entry_ref=entry_ref;self.post=post
        self.worker_id=secrets.token_urlsafe(24)
        self._task=None;self._jobs={};self._closed=False
        self.prepare_turn=prepare_turn

    def start(self):
        # Called on the original native loop. Runtime is retained explicitly;
        # asyncio.run/new loops never operate the existing native Runtime.
        if self._task is None:self._task=asyncio.create_task(self.run())

    async def call(self,action,**payload):
        result=await asyncio.to_thread(self.post,{'purpose':'jiuwen.native','action':action,
            'workerId':self.worker_id,**payload})
        value=result.get('data')
        if not isinstance(value,dict):raise ValueError('native_control_receipt_invalid')
        return value

    async def run(self):
        while not self._closed:
            try:
                reply=await self.call('poll',entryRef=self.entry_ref,source=self.source,
                    features=['request_turn'] if self.prepare_turn else [])
                if reply.get('retire') is True:return
                command=reply.get('command')
                if command is not None:
                    command_id=command['commandId']
                    if command_id not in self._jobs:
                        self._jobs[command_id]=asyncio.create_task(self.execute(command))
            except asyncio.CancelledError:raise
            except Exception:
                # Disconnection never creates a new worker/request/session.
                pass
            await asyncio.sleep(0.5)

    async def report(self,command,report):
        return await self.call('report',commandId=command['commandId'],report=report)

    async def _describe(self,sid):
        actual=await self.runtime.describe_session(session_id=sid)
        if (actual is None or actual.session_id!=sid or actual.channel_id!=self.source['channelId'] or
                actual.user_id!=self.source['userId'] or actual.project_dir!=self.source['projectDir'] or
                actual.project_id!=self.source['projectId']):
            raise ValueError('native_session_owner_mismatch')
        return actual

    async def execute(self,command):
        # One release authorizes one dispatch. No broad retry wraps this body.
        turn=None;sent=False;final=False;unreported=False
        try:
            await self._describe(self.source['sessionId'])
            sid=command['sessionId']
            if sid is None:
                prepared=await self.runtime.prepare_session_create(SessionCreateInput(
                    channel_id=self.source['channelId'],user_id=self.source['userId'],
                    create_token=command['commandId'],persist_session=True,persist_session_supplied=True,
                    mode=self.source['mode'],work_mode=self.source['workMode'],
                    project_id=self.source['projectId'],project_dir=self.source['projectDir'],
                    cwd=self.source['workspace'],title='IoA '+command['operationId'][:80]))
                sid=prepared.result.session_id;unreported=True
                # Actual prepare-created identity is reported before any model
                # dispatch. Lost receipt is unknown and is never created again.
                await self.report(command,{'stage':'created','sessionId':sid})
                unreported=False
                result=await self.runtime.commit_session_provision(prepared,timing=prepared.commit_timing)
                if result.session_id!=sid:raise ValueError('native_create_identity_changed')
            await self._describe(sid)
            if command.get('execution') is not None:
                if self.prepare_turn is None:raise ValueError('native_definition_unavailable')
                async def callback(call_id,name,arguments):
                    result=await self.call('sdk_tool',commandId=command['commandId'],sessionId=sid,
                        requestId=command['plannedRequestId'],callId=call_id,name=name,arguments=arguments)
                    return result
                turn=await self.prepare_turn(self.runtime,self.source,sid,command,callback)
                if turn is None:raise ValueError('native_definition_unavailable')
            sent=True
            snapshot=await self.runtime.send_session_message(source_session_id=self.source['sessionId'],
                target_session_id=sid,content=command['text'],request_id=command['plannedRequestId'])
            identity=(snapshot.session_id,snapshot.request_id,snapshot.execution_id,snapshot.generation)
            if identity[:2]!=(sid,command['plannedRequestId']):raise ValueError('native_request_identity_changed')
            while True:
                try:
                    receipt=await self.report(command,{'stage':'snapshot','snapshot':snapshot_wire(snapshot),
                        'guardError':getattr(turn,'failure',None) if turn is not None else None})
                    if turn is not None:turn.accept(receipt.get('bindingAck') is True)
                    if snapshot.state.terminal:
                        final=True;return
                except asyncio.CancelledError:raise
                except Exception:
                    # Retry only this observation/receipt, never native submit.
                    if snapshot.state.terminal:
                        await asyncio.sleep(0.5)
                        continue
                await asyncio.sleep(0.5)
                snapshot=self.runtime.get_session_execution(identity[2])
                if snapshot is None:raise ValueError('native_snapshot_unknown')
                if (snapshot.session_id,snapshot.request_id,snapshot.execution_id,snapshot.generation)!=identity:
                    raise ValueError('native_execution_identity_changed')
        except asyncio.CancelledError:
            # Cancelling transport observation is not a native cancellation ACK.
            raise
        except Exception:
            # Zero dispatch is reported only when no unreported Session exists;
            # the next SDK attempt then reuses the reported Session.
            try:await self.report(command,{'stage':'error' if sent or unreported else 'preflight_error'})
            except Exception:pass
        finally:
            # Undispatched or terminal releases now; unknown keeps the inert guard.
            if turn is not None:await turn.finish(final or not sent)

    def close(self):
        self._closed=True
        if self._task is not None:self._task.cancel()
        # Native jobs are not cancelled as an implication of a Rail reload.
