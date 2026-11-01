"""Read-only network recovery never replays token renewal or business writes."""
import httpx
import pytest
from a2c.codec import digest_bytes
from ioalab_sdk.runtime.session import AccountSession

BLOB=b'original immutable verifier bytes'
URI='ioalab://pkg/'+digest_bytes(BLOB)


def reader(http):
    session=AccountSession.__new__(AccountSession)
    session.base='https://platform.invalid';session.http=http
    acquired=[]
    def bearer():
        acquired.append(True)
        return 'isolated-test-token'
    session.bearer=bearer
    return session,acquired


def test_package_read_recovers_disconnect_and_truncation_without_repeating_credentials():
    requests=[]
    def serve(request):
        requests.append(request)
        if len(requests)==1:raise httpx.ReadError('connection lost',request=request)
        if len(requests)==2:raise httpx.RemoteProtocolError('body truncated',request=request)
        return httpx.Response(200,content=BLOB)
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        session,acquired=reader(http)
        assert session.fetch_package(URI)==BLOB
    assert len(acquired)==1 and len(requests)==3
    assert {(r.method,str(r.url)) for r in requests}=={('GET','https://platform.invalid/api/v1/packages/'+digest_bytes(BLOB))}
    assert all(r.headers['Authorization']=='Bearer isolated-test-token' and not r.content for r in requests)


def test_package_read_stops_after_bounded_transport_failures():
    requests=[]
    def serve(request):
        requests.append(request)
        raise httpx.ReadError('original read still unavailable',request=request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        session,acquired=reader(http)
        with pytest.raises(httpx.ReadError,match='original read still unavailable'):
            session.fetch_package(URI)
    assert len(requests)==3 and len(acquired)==1


@pytest.mark.parametrize('status',[401,404,500])
def test_package_http_status_is_not_retried(status):
    requests=[]
    def serve(request):
        requests.append(request);return httpx.Response(status)
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        session,_=reader(http)
        with pytest.raises(httpx.HTTPStatusError):session.fetch_package(URI)
    assert len(requests)==1


def test_package_digest_mismatch_is_not_retried():
    requests=[]
    def serve(request):
        requests.append(request);return httpx.Response(200,content=b'tampered bytes')
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        session,_=reader(http)
        with pytest.raises(ValueError,match='digest mismatch'):session.fetch_package(URI)
    assert len(requests)==1


def test_package_timeout_does_not_expand_the_existing_wait():
    requests=[]
    def serve(request):
        requests.append(request);raise httpx.ReadTimeout('original timeout',request=request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        session,_=reader(http)
        with pytest.raises(httpx.ReadTimeout):session.fetch_package(URI)
    assert len(requests)==1


def test_invalid_package_reference_does_not_acquire_credentials_or_send():
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected HTTP request'))) as http:
        session,acquired=reader(http)
        with pytest.raises(ValueError):session.fetch_package('https://other.invalid/package')
    assert not acquired


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_package_disconnect_does_not_abort_original_two_period_production(platform,tmp_path,monkeypatch,formation):
    from test_resident_multiperiod import run_two_periods
    original_fetch=AccountSession.fetch_package
    wrapped=set();lost=[]
    def fetch(account,uri):
        if account.home.is_relative_to(tmp_path/'provider') and account not in wrapped:
            wrapped.add(account)
            original_get=account.http.get
            def get(url,*args,**kwargs):
                if str(url).startswith(account.base+'/api/v1/packages/') and not lost:
                    lost.append(uri)
                    raise httpx.ReadError('First original verifier package read disconnected',request=httpx.Request('GET',url))
                return original_get(url,*args,**kwargs)
            monkeypatch.setattr(account.http,'get',get)
        return original_fetch(account,uri)
    monkeypatch.setattr(AccountSession,'fetch_package',fetch)
    result=run_two_periods(platform,tmp_path,monkeypatch,formation,'normal')
    assert len(lost)==1 and result['closed'] and result['reputationCompleted']==1
    assert result['productionRounds']==4 and len(result['paymentRecords'])==3
