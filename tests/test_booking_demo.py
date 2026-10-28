"""Local booking concurrency, idempotency and cancellation; verifier stays in WASM."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from catalog import definitions

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('booking_app',ROOT/'examples/booking_app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


def test_capacity_cancel_and_retries(tmp_path):
    config=next(d['sample'] for d in definitions() if d['id']=='nail-booking-reliable')
    bookings=app.Bookings(config,tmp_path/'bookings.db')
    def attempt(n):
        try:return bookings.book(str(n),'测试客户'+str(n),'纯色美甲','2026-10-29T11:00')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=10) as workers:results=list(workers.map(attempt,range(10)))
    accepted=[r for r in results if r]
    assert len(accepted)==2 and len(bookings.list())==2
    first=accepted[0]
    assert bookings.book(first['request'],first['name'],first['service'],first['start'])['id']==first['id']
    with pytest.raises(ValueError):bookings.book(first['request'],'不同客户',first['service'],first['start'])
    bookings.cancel(first['id']);assert attempt(99)
    with pytest.raises(ValueError):bookings.book('late','客户','延长甲','2026-10-29T19:30')


def test_booking_verifiers_good_and_bad(tmp_path):
    from tools.check_services import check
    from ioalab.runtime.packages import PackageStore
    import copy
    packages=PackageStore(tmp_path/'packages')
    for original in [d for d in definitions() if d['id'].startswith('nail-booking-')]:
        assert check(original,packages)==[]
        bad=copy.deepcopy(original);bad['sample']['capacity']=3;bad['expect_fail']=True
        assert check(bad,packages)==[]
    bad=copy.deepcopy(original);bad['sample']['allowCancel']=False;bad['expect_fail']=True
    assert check(bad,packages)==[]


def test_booking_spec_rejects_duration_alias_and_out_of_range_minutes():
    from a2c.schema import validate_filled
    from a2c.errors import A2CError
    import copy
    for d in [d for d in definitions() if d['id'].startswith('nail-booking-')]:
        schema=d['deliverable']['specSchema'];good=copy.deepcopy(d['sample_spec'])
        validate_filled(schema,good,'booking')
        wrong=copy.deepcopy(good);wrong['service1Duration']=wrong.pop('service1Minutes')
        with pytest.raises(A2CError):validate_filled(schema,wrong,'booking')
        for minutes in (0,241,75.5,True):
            wrong=copy.deepcopy(good);wrong['service1Minutes']=minutes
            with pytest.raises(A2CError):validate_filled(schema,wrong,'booking')


def test_browser_cancel_request_shape(tmp_path):
    import socket,subprocess,sys,time,json,httpx
    config=next(d['sample'] for d in definitions() if d['id']=='nail-booking-reliable')
    path=tmp_path/'config.json';path.write_text(json.dumps(config),encoding='utf-8')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    child=subprocess.Popen([sys.executable,str(ROOT/'examples/booking_app.py'),'--config',str(path),'--database',str(tmp_path/'book.db'),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as http:
            until=time.monotonic()+10
            while True:
                try:http.get('/config').raise_for_status();break
                except httpx.ConnectError:
                    if time.monotonic()>until:raise
                    time.sleep(.05)
            row=http.post('/book',json={'request':'browser-shape','name':'客户','service':'纯色美甲','start':'2026-10-29T11:00'}).raise_for_status().json()
            assert http.post('/cancel',json={'id':row['id']}).raise_for_status().json()['status']=='cancelled'
            assert http.get('/bookings').json()[0]['cancelled']==1
            assert http.post('/cancel',json={'ident':row['id']}).status_code==400
    finally:child.terminate();child.wait(5)
