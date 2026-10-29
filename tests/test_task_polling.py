"""A temporary broken read must not force another paid proposal submission."""
import httpx
import pytest
from a2a.client.errors import A2AClientError
from a2c.binding import A2ATransport
from ioalab_sdk.transport import TaskPollingTransport


def test_poll_recovers_network_reads_with_same_task(monkeypatch):
    calls=[]
    expected=object()
    def read(self,to,task_id):
        calls.append((to,task_id))
        if len(calls)<3:
            try:raise httpx.ReadError('transient loopback read')
            except httpx.ReadError as cause:raise A2AClientError('Network communication error') from cause
        return expected
    monkeypatch.setattr(A2ATransport,'get_task',read)
    transport=TaskPollingTransport.__new__(TaskPollingTransport)
    assert transport.get_task('http://provider','existing-task') is expected
    assert calls==[('http://provider','existing-task')]*3


def test_poll_never_retries_protocol_rejection(monkeypatch):
    calls=[]
    def read(self,to,task_id):
        calls.append(task_id);raise A2AClientError('Authoritative protocol rejection')
    monkeypatch.setattr(A2ATransport,'get_task',read)
    with pytest.raises(A2AClientError,match='Authoritative'):
        TaskPollingTransport.__new__(TaskPollingTransport).get_task('http://provider','task')
    assert calls==['task']
