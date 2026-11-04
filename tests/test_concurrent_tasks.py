"""Persisted tasks must not race task creation or overwrite newer endpoint state."""
import threading
from types import SimpleNamespace
from a2c.common import Task
from ioalab_sdk.adapters.endpoint import EndpointSession


def test_task_persistence_serializes_with_endpoint_mutation():
    endpoint=EndpointSession.__new__(EndpointSession)
    endpoint.lock=threading.RLock()
    provider=SimpleNamespace(tasks={})
    endpoint.provider=provider
    first=Task(id='original',op='work.start')
    provider.tasks[first.id]=first
    saving=threading.Event();release=threading.Event();mutated=threading.Event()
    failures=[]
    persisted={}

    class Store:
        def put(self,table,key,value):
            saving.set()
            assert release.wait(3),'Test failed to release persistence'
            persisted[key]=value

    endpoint.store=Store()
    def save():
        try:endpoint.save_tasks()
        except BaseException as exc:failures.append(exc)
    def prepare_task(message):
        provider.tasks['next']=Task(id='next',op='delivery.fetch')
        return {'taskId':'next'}
    endpoint._prepare=prepare_task
    def mutate():
        try:
            assert endpoint.prepare({})=={'taskId':'next'}
            mutated.set()
        except BaseException as exc:failures.append(exc)
    writer=threading.Thread(target=save);writer.start()
    assert saving.wait(3)
    creator=threading.Thread(target=mutate);creator.start()
    try:assert not mutated.wait(.15),'Task creation raced an in-progress persistence snapshot'
    finally:
        release.set();writer.join(3);creator.join(3)
    assert not failures and mutated.is_set()
    endpoint.save_tasks()
    assert set(persisted)==set(provider.tasks) and len(persisted)==2
    assert persisted[first.id]['op']=='work.start'
