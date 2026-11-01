"""A stop signal drains the original real process without replaying queued work."""
import os
from pathlib import Path
import signal

import pytest

from conftest import wait_for
from ioalab_sdk.lease import Lease
from test_resident_runtime import configuration,process,query,uid


@pytest.mark.parametrize('channel',['python-signal','os-signal'])
def test_stop_signal_waits_for_active_job_preserves_queue_and_original_identity(platform,tmp_path,channel):
    if channel=='os-signal' and os.name=='nt':
        pytest.skip('Windows TerminateProcess is not POSIX SIGTERM; Linux CI sends the actual OS signal')
    config,user=configuration(platform,tmp_path,service=False)
    receipt=tmp_path/'receipts.txt';release=receipt.with_suffix('.release')
    with process(config,fixture=True) as (child,local,original):
        interaction=uid()
        first=local.call('test.enqueue',{'interaction':interaction,'session':'same-original-session'})['data']['reference']
        wait_for(lambda:receipt.exists() and receipt.read_text().strip()==first)
        queued=local.call('test.enqueue',{'interaction':interaction,'session':'same-original-session'})['data']['reference']
        if channel=='os-signal':child.send_signal(signal.SIGTERM)
        else:local.call('test.signal',{})
        try:
            wait_for(lambda:(query(local) or {}).get('stopping'))
            assert child.poll() is None
            assert local.call('test.enqueue',{'interaction':uid()})['error']['code']=='closed'
            status=local.call('operations.get',{'reference':{'kind':'job','id':queued}})
            assert status['data']['status']=='queued'
            with pytest.raises(RuntimeError):Lease(tmp_path/'state/client.lock',timeout=0)
            assert receipt.read_text().splitlines()==[first]
            if channel=='os-signal':child.send_signal(signal.SIGTERM)
        finally:
            release.write_text('release original active fixture')
        assert child.wait(timeout=20)==0
    with Lease(tmp_path/'state/client.lock',timeout=0):pass
    with process(config,fixture=True) as (_,local,restarted):
        assert restarted['identities']==original['identities']
        assert restarted['bootId']!=original['bootId']
        wait_for(lambda:local.call('operations.get',{'reference':{'kind':'job','id':queued}})['data']['status']=='completed')
        assert receipt.read_text().splitlines()==[first,queued]
        assert platform.wallets.balance(user['id'])==100
