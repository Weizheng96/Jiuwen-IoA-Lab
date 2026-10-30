"""Actual scheduling exclusion, waiting, unknown effects and bounded queues."""
import threading
import time
import uuid

import pytest

from conftest import wait_for
from ioalab_sdk.runtime import RuntimeStore,RuntimeFault
from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome


def uid():return str(uuid.uuid4())


@pytest.fixture
def store(tmp_path):
    value=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['a','b'],owner_id='owner')
    yield value
    value.close()


def submit(queue,interaction,*,handler='model',session=None,payload=None,operation=None,deadline=None):
    return queue.submit('a',interaction,handler,payload or {},operation_id=operation or uid(),session=session,deadline=deadline)['reference']


def test_parallel_models_exclude_same_interaction_and_real_session_without_blocking_control(store):
    gate=threading.Event();started=set();lock=threading.Lock()
    def run(job):
        with lock:started.add(job['interaction'])
        assert gate.wait(5)
        return JobOutcome('completed',{'done':True})
    queue=Scheduler(store,{'model':JobHandler(run,'v1'),
                           'control':JobHandler(lambda job:JobOutcome('completed',{'control':True}),'v1',False)},max_models=2).start()
    try:
        first=submit(queue,'one',session='session1')
        second=submit(queue,'two',session='session2')
        wait_for(lambda:queue.status()['active']==2)
        blocked=[submit(queue,'one',session='session3'),submit(queue,'three',session='session2'),submit(queue,'four')]
        control=submit(queue,'control',handler='control')
        wait_for(lambda:queue.get(control)['status']=='completed')
        assert all(queue.get(job)['status']=='queued' for job in blocked)
        assert started=={'one','two'}
        gate.set()
        wait_for(lambda:all(queue.get(job)['status']=='completed' for job in [first,second,*blocked]))
        assert queue.status()['error'] is None
    finally:gate.set();queue.close()


def test_waiting_releases_model_slot_and_resumes_only_original_session(store):
    def run(job):
        return JobOutcome('completed' if job['continuation'] else 'waiting',{'real':job['continuation']},job['session'])
    queue=Scheduler(store,{'model':JobHandler(run,'v1')},max_models=1).start()
    try:
        original=submit(queue,'waiting',session='fixed-session')
        wait_for(lambda:queue.get(original)['status']=='waiting')
        blocked=submit(queue,'another-request',session='fixed-session')
        independent=submit(queue,'independent',session='independent-session')
        wait_for(lambda:queue.get(independent)['status']=='waiting')
        assert queue.get(blocked)['status']=='queued'
        wait_for(lambda:queue.idle)
        queue.resume(original,{'answerEvent':'trusted-host-event'})
        wait_for(lambda:queue.get(original)['status']=='completed')
        assert queue.get(original)['sessionId']=='fixed-session'
    finally:queue.close()


def test_unknown_effect_blocks_interaction_and_session_until_role_reconciles(store):
    calls=[]
    def run(job):
        calls.append(job['interaction'])
        if job['interaction']=='first':raise OSError('Private details must not appear in the result')
        return JobOutcome('completed',{'ok':True})
    queue=Scheduler(store,{'model':JobHandler(run,'v1')}).start()
    try:
        original=submit(queue,'first',session='same-session')
        wait_for(lambda:queue.get(original)['status']=='unknown')
        blocked=submit(queue,'second',session='same-session')
        other=submit(queue,'third',session='other-session')
        wait_for(lambda:queue.get(other)['status']=='completed')
        assert queue.get(blocked)['status']=='queued' and 'Private' not in str(queue.get(original))
        with pytest.raises(RuntimeFault):queue.resume(original,{})
        queue.resolve(original,JobOutcome('completed',{'receipt':'original-tool-receipt'},'same-session'))
        wait_for(lambda:queue.get(blocked)['status']=='completed')
        assert calls.count('first')==1
    finally:queue.close()


def test_live_native_wait_yields_model_but_holds_original_session_and_reacquires_limit(store):
    answer=threading.Event();other_started=threading.Event();release_other=threading.Event();resumed=threading.Event()
    def run(job):
        if job['interaction']=='native':
            with queue.user_wait(job['id']):assert answer.wait(10)
            resumed.set()
        elif job['interaction']=='other':
            other_started.set()
            assert release_other.wait(10)
        return JobOutcome('completed',{},job['session'])
    queue=Scheduler(store,{'model':JobHandler(run,'v1')},max_models=1).start()
    try:
        original=submit(queue,'native',session='native-session')
        wait_for(lambda:queue.status()['waitingForUser']==1)
        blocked=submit(queue,'same-session',session='native-session')
        independent=submit(queue,'other',session='other-session')
        assert other_started.wait(5)
        assert queue.get(blocked)['status']=='queued'
        answer.set()
        assert not resumed.wait(.2)  # The answer does not overbook the model slot.
        release_other.set()
        wait_for(lambda:all(queue.get(job)['status']=='completed' for job in (original,blocked,independent)))
        assert resumed.is_set() and queue.status()['waitingForUser']==0
    finally:answer.set();release_other.set();queue.close()


def test_queue_replay_conflicts_expiry_and_drain(store):
    calls=[]
    queue=Scheduler(store,{'model':JobHandler(lambda job:(calls.append(job['id']) or JobOutcome('completed',{})),'v1')},max_pending=1)
    operation=uid()
    first=submit(queue,'first',operation=operation,deadline=time.time()-1)
    # Exact original deadline is part of the immutable input, not recalculated on retry.
    with store.lock:deadline=store.db.execute('SELECT deadline FROM runtime_jobs WHERE id=?',(first,)).fetchone()[0]
    assert submit(queue,'first',operation=operation,deadline=deadline)==first
    with pytest.raises(RuntimeFault,match='changed'):submit(queue,'first',operation=operation,payload={'changed':True})
    with pytest.raises(RuntimeFault,match='full'):submit(queue,'second')
    queue.start()
    try:
        wait_for(lambda:queue.get(first)['status']=='failed')
        assert calls==[]
        queue.drain()
        with pytest.raises(RuntimeFault,match='draining'):submit(queue,'second')
    finally:queue.close()


def test_new_jobs_wait_for_current_execution_eligibility(store):
    permitted=threading.Event()
    called=[]
    queue=Scheduler(store,{'model':JobHandler(lambda job:(called.append(job['id']) or JobOutcome('completed',{})),'v1')},
                    can_dispatch=permitted.is_set).start()
    try:
        original=submit(queue,'offline')
        assert queue.get(original)['status']=='queued' and called==[]
        permitted.set()
        wait_for(lambda:queue.get(original)['status']=='completed')
        assert called==[original]
    finally:queue.close()


def test_restart_marks_running_unknown_but_keeps_queued_and_handler_revision(store):
    queue=Scheduler(store,{'model':JobHandler(lambda job:JobOutcome('completed',{}),'v1')})
    running=submit(queue,'lost',session='lost-session')
    operation=uid()
    queued=submit(queue,'queued',operation=operation)
    blocked=submit(queue,'blocked',session='lost-session')
    with store.transaction() as conn:conn.execute("UPDATE runtime_jobs SET state='running' WHERE id=?",(running,))
    queue.close()
    replacement=Scheduler(store,{'model':JobHandler(lambda job:JobOutcome('completed',{}),'v2')}).start()
    try:
        assert replacement.get(running)['status']=='unknown'
        assert replacement.get(queued)['status']=='queued'
        with pytest.raises(RuntimeFault,match='changed'):submit(replacement,'queued',operation=operation)
    finally:replacement.close()
    restored=Scheduler(store,{'model':JobHandler(lambda job:JobOutcome('completed',{}),'v1')}).start()
    try:
        wait_for(lambda:restored.get(queued)['status']=='completed')
        assert restored.get(blocked)['status']=='queued'
        assert restored.get(running)['status']=='unknown'
    finally:restored.close()
