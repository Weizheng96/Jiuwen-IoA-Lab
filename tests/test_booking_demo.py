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
