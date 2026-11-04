"""Persisted tasks must not race task creation or overwrite newer endpoint state."""
import threading
from ioalab_sdk.provider import DurableProvider


def test_task_persistence_serializes_with_endpoint_mutation():
    provider=DurableProvider.__new__(DurableProvider)
    provider._lock=threading.RLock()
    provider._next_task_id=None
    provider.tasks={}
    first=provider.new_task('work.start')
    saving=threading.Event();release=threading.Event();mutated=threading.Event()
    failures=[]
    persisted={}

    class Store:
        def put(self,table,key,value):
            saving.set()
            assert release.wait(3),'Test failed to release persistence'
            persisted[key]=value

    provider.store=Store()
    def save():
        try:provider.save_tasks()
        except BaseException as exc:failures.append(exc)
    def mutate():
        try:
            provider.new_task('delivery.fetch')
            mutated.set()
        except BaseException as exc:failures.append(exc)
    writer=threading.Thread(target=save);writer.start()
    assert saving.wait(3)
    creator=threading.Thread(target=mutate);creator.start()
    try:assert not mutated.wait(.15),'Task creation raced an in-progress persistence snapshot'
    finally:
        release.set();writer.join(3);creator.join(3)
    assert not failures and mutated.is_set()
    provider.save_tasks()
    assert set(persisted)==set(provider.tasks) and len(persisted)==2
    assert persisted[first.id]['op']=='work.start'
