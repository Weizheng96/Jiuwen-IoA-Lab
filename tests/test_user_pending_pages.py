"""Browser-only bounded pages over the original confirmation records."""
from urllib.parse import quote,urlsplit

import httpx
import pytest
from contextlib import contextmanager

from ioalab_sdk.runtime.ipc import IpcServer
from ioalab_sdk.runtime.user_ui import UserUi
from test_user_confirmations import manager,binding,approve
from ioalab_sdk.runtime import ConfirmationDeadline,RuntimeStore
from ioalab_sdk.runtime.confirmations import Confirmations


@contextmanager
def owner_browser(confirmations,tmp_path):
    ui=UserUi(confirmations);server=IpcServer(tmp_path,'pending-pages',lambda *_:{},user_ui=ui).start()
    try:
        with httpx.Client(base_url=ui.origin,trust_env=False) as browser:
            entry=browser.post('/user/session',json={'token':urlsplit(ui.entry_url()).fragment},headers={'Origin':ui.origin})
            assert entry.status_code==200
            yield ui,browser,entry.json()['csrf']
    finally:server.close()


def test_pending_pages_are_not_hidden_by_newer_history(manager,tmp_path):
    confirmations,clock=manager
    expected=[]
    for _ in range(101):
        expected.append(confirmations.ensure(binding())['reference']);clock[0]+=1
    for _ in range(110):
        approve(confirmations,confirmations.ensure(binding()));clock[0]+=1
    ui=UserUi(confirmations)
    server=IpcServer(tmp_path,'pending-pages',lambda *_:{},user_ui=ui).start()
    try:
        with httpx.Client(base_url=ui.origin,trust_env=False) as browser:
            entered=browser.post('/user/session',json={'token':urlsplit(ui.entry_url()).fragment},headers={'Origin':ui.origin})
            assert entered.status_code==200
            first=browser.get('/user/api').json()
            assert len(first['confirmations'])==50
            assert all(item['status']=='pending' for item in first['confirmations'])
            assert first['page']['total']==101 and first['page']['pendingCount']==101
            found=[item['reference'] for item in first['confirmations']]
            cursor=first['page']['nextCursor']
            while cursor:
                response=browser.get('/user/api?view=pending&cursor='+quote(cursor,safe=''))
                assert response.status_code==200
                page=response.json()
                assert len(page['confirmations'])<=50
                found.extend(item['reference'] for item in page['confirmations'])
                cursor=page['page']['nextCursor']
            assert found==expected
    finally:server.close()


def test_page_queries_and_original_lookup_stay_in_the_browser_channel(manager,tmp_path):
    confirmations,_=manager;value=confirmations.ensure(binding())
    with owner_browser(confirmations,tmp_path) as (ui,browser,csrf):
        with httpx.Client(base_url=ui.origin,trust_env=False) as stranger:
            assert stranger.get('/user/api?view=history').status_code==403
            assert stranger.get('/user/api?reference='+value['reference']).status_code==403
        assert browser.get('/user/api?view=history',headers={'Origin':'https://foreign.invalid'}).status_code==403
        for query in ('view=other','cursor=not-base64!','cursor=','view=pending&view=history',
                      'limit=100000','reference='+value['reference']+'&view=pending'):
            assert browser.get('/user/api?'+query).status_code==400,query
        # Reading a known original status never supplies decision authority.
        looked=browser.get('/user/api?reference='+value['reference']).json()['confirmation']
        assert looked['reference']==value['reference'] and looked['status']=='pending'
        assert 'authority' not in looked
        body={'reference':value['reference'],'bindingDigest':value['bindingDigest'],'decision':'approve'}
        assert browser.post('/user/decision?view=pending',json=body,headers={'Origin':ui.origin,'X-Ioa-Csrf':csrf}).status_code==403
        assert confirmations.get(value['reference'])['status']=='pending'


def test_pages_preserve_hard_expiry_soft_due_and_real_final_decisions(manager,tmp_path):
    confirmations,clock=manager
    hard=confirmations.ensure(binding(),deadline=ConfirmationDeadline(expires_at=1030.))
    soft=confirmations.ensure(binding(),deadline=ConfirmationDeadline(response_due_at=1010.))
    rejected=confirmations.ensure(binding())
    confirmations.decide(rejected['reference'],rejected['bindingDigest'],'reject',authority=confirmations.user_channel(),source='original-user')
    clock[0]=1040.
    with owner_browser(confirmations,tmp_path) as (_,browser,_csrf):
        page=browser.get('/user/api').json()
        assert [item['reference'] for item in page['confirmations']]==[soft['reference']]
        assert page['confirmations'][0]['responseOverdue'] is True
        assert page['confirmations'][0]['expiresAt'] is None
        history=browser.get('/user/api?view=history').json()['confirmations']
        assert {item['reference']:item['status'] for item in history}=={hard['reference']:'expired',rejected['reference']:'rejected'}
        assert confirmations.get(rejected['reference'])['status']=='rejected'


def test_one_accounts_two_identities_are_not_truncated_by_the_other(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor','second'],owner_id='owner')
    confirmations=Confirmations(store)
    older=confirmations.ensure(binding())
    for _ in range(105):approve(confirmations,confirmations.ensure({**binding(),'actor':'second'}))
    try:
        with owner_browser(confirmations,tmp_path) as (_,browser,_csrf):
            page=browser.get('/user/api').json()
            assert [item['reference'] for item in page['confirmations']]==[older['reference']]
            assert page['page']['total']==1
    finally:store.close()
