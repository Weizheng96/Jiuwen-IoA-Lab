"""The declared query wait outlives short transport defaults without extending writes."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from ioalab_sdk.runtime.ipc import IpcServer,LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault


@pytest.mark.parametrize('waiting',[True,False])
def test_local_query_budget_respects_explicit_wait_but_other_calls_keep_their_deadline(tmp_path,waiting):
    entered=threading.Event();release=threading.Event()
    def dispatch(operation,operation_id,args):
        entered.set();assert release.wait(5)
        return {'operationId':operation_id,'status':'completed','data':{'read':True},'error':None}
    server=IpcServer(tmp_path,'query-budget',dispatch);server.start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            query=pool.submit(LocalClient(tmp_path,timeout=.1).call,'test.read',{'waitSeconds':1} if waiting else {})
            try:
                assert entered.wait(2)
                threading.Event().wait(.3)
                if waiting:assert not query.done()
                else:
                    with pytest.raises(RuntimeFault,match='response unavailable'):query.result(timeout=1)
            finally:release.set()
            if waiting:assert query.result(timeout=2)['status']=='completed'
    finally:server.close()
