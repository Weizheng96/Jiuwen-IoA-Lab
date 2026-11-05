"""Installed read-only readiness never logs in, follows redirects, or accepts another core."""
import io
import json
from urllib.error import HTTPError,URLError

import pytest
from ioalab import readiness


def test_delayed_core_becomes_ready_without_login_or_proxy(monkeypatch):
    clock=[0];calls=[]
    class Endpoint:
        def open(self,url,timeout):
            calls.append((url,timeout))
            if len(calls)==1:raise URLError('still starting')
            return io.BytesIO(json.dumps({'publicUrl':'https://ioa.example'}).encode())
    def opener(*handlers):
        assert handlers[0].proxies=={} and isinstance(handlers[1],readiness.NoRedirect)
        return Endpoint()
    monkeypatch.setattr(readiness,'build_opener',opener)
    monkeypatch.setattr(readiness.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(readiness.time,'sleep',lambda duration:clock.__setitem__(0,clock[0]+duration))
    readiness.wait_ready('http://127.0.0.1:8750','https://ioa.example',timeout=1)
    assert len(calls)==2 and all(url=='http://127.0.0.1:8750/api/config' for url,_ in calls)


@pytest.mark.parametrize('failure',['wrong-core','redirect','unavailable'])
def test_wrong_identity_redirect_and_expired_deadline_fail_closed(monkeypatch,failure):
    calls=[]
    class Endpoint:
        def open(self,url,timeout):
            calls.append(url)
            if failure=='unavailable':raise URLError('offline')
            if failure=='redirect':raise HTTPError(url,302,'Redirect',{},None)
            return io.BytesIO(b'{"publicUrl":"https://foreign.example"}')
    monkeypatch.setattr(readiness,'build_opener',lambda *handlers:Endpoint())
    with pytest.raises(RuntimeError if failure=='unavailable' else ValueError):
        readiness.wait_ready('http://127.0.0.1:8750','https://ioa.example',timeout=0)
    assert len(calls)==1
    assert readiness.NoRedirect().redirect_request(None,None,302,'',{},'https://foreign.example') is None


@pytest.mark.parametrize('timeout',[float('nan'),float('inf'),-1,601])
def test_invalid_timeout_rejected_without_network(monkeypatch,timeout):
    monkeypatch.setattr(readiness,'build_opener',lambda *args:pytest.fail('Invalid timeout reached network'))
    with pytest.raises(ValueError):readiness.wait_ready('http://localhost','https://ioa.example',timeout=timeout)


def test_cli_receipt_matches_installed_command_contract(monkeypatch,capsys):
    monkeypatch.setattr(readiness,'wait_ready',lambda *args,**kwargs:None)
    assert readiness.main(['--url','http://127.0.0.1:8750','--expected-public-url','https://ioa.example'])==0
    assert json.loads(capsys.readouterr().out)=={'status':'completed','ready':True}
