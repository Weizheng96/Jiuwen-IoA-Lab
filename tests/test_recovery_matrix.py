"""Stage × fault recovery matrix for both roles: every cell names a real test; new cells close the gaps.

Faults: disconnect (the socket drops while an original operation is in flight, or another
executor takes over), restart (the resident process stops and a new one restores local state),
lost (the platform or peer committed, but the local response was lost), unknown (the actual
effect is unknown and must be reconciled from original receipts, never re-run generically).
"""
import ast
from pathlib import Path

import pytest

from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import ResidentRuntime
from ioalab_sdk.runtime.connection import AgentConnection
from ioalab_sdk.runtime.ipc import LocalClient
from test_assistant_role import user_decision
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants,uid
from test_resident_fulfillment import operation
from conftest import wait_for


TESTS=Path(__file__).resolve().parent

MATRIX={
    ('authorize','disconnect'):['test_delegation_scopes.py::test_ws_scope_receipt_uses_same_http_recovery_and_rejects_other_owner'],
    ('authorize','restart'):['test_recovery_matrix.py::test_authorization_waiting_for_the_user_survives_an_assistant_restart'],
    ('authorize','lost'):['test_delegation_scopes.py::test_resident_authorization_needs_browser_and_recovers_lost_response_without_new_grant'],
    ('authorize','unknown'):['test_delegation_scopes.py::test_resident_authorization_needs_browser_and_recovers_lost_response_without_new_grant',
                             'test_delegation_scopes.py::test_concurrent_registration_and_grant_revision_have_one_authoritative_result'],
    ('contract','disconnect'):['test_recovery_matrix.py::test_formation_survives_a_socket_drop_during_contract_binding'],
    ('contract','restart'):['test_recovery_matrix.py::test_contract_choice_waiting_for_the_user_survives_an_assistant_restart'],
    ('contract','lost'):['test_resident_contracts.py::test_lost_signed_result_recovers_original_user_signature_without_repeating_review',
                         'test_contract_binding.py::test_lost_formation_reply_recovers_original_slot_one_and_same_registration',
                         'test_resident_delegation.py::test_lost_market_reply_recovers_original_signatures_without_repeating_model_or_payment'],
    ('contract','unknown'):['test_resident_delegation.py::test_unknown_host_is_reported_without_faking_failure_or_reexecuting_it',
                            'test_provider_task_recovery.py::test_exception_without_a_completed_host_round_never_gets_a_resume_permit'],
    ('fulfillment','disconnect'):['test_runtime_delivery.py::test_reconnect_replays_original_operation_after_lost_response',
                                  'test_resident_delegation.py::test_real_socket_takeover_after_market_preparation_cannot_publish_old_generation_signature'],
    ('fulfillment','restart'):['test_resident_multiperiod.py::test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments',
                               'test_provider_production_recovery.py::test_verified_production_resumes_same_contract_session_and_completes_payment'],
    ('fulfillment','lost'):['test_resident_fulfillment.py::test_full_contract_with_actual_user_step_delivery_wasm_payment_close_and_reputation',
                            'test_provider_production_recovery.py::test_lost_resume_response_and_parallel_retransmits_use_one_queue_receipt'],
    ('fulfillment','unknown'):['test_resident_runtime.py::test_process_crash_retains_unknown_effect_and_does_not_repeat_it',
                               'test_provider_production_recovery.py::test_unknown_host_round_is_never_runtime_verified',
                               'test_provider_production_recovery.py::test_unresolved_tool_effect_cannot_be_overridden_by_owner_review'],
    ('acceptance','disconnect'):['test_recovery_matrix.py::test_acceptance_survives_a_socket_drop_during_the_acceptance_notice'],
    ('acceptance','restart'):['test_resident_multiperiod.py::test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments'],
    ('acceptance','lost'):['test_resident_multiperiod.py::test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments',
                           'test_fulfillment_payment_guards.py::test_voucher_without_positive_notification_does_not_finish_original_job'],
    ('acceptance','unknown'):['test_resident_fulfillment.py::test_full_contract_with_actual_user_step_delivery_wasm_payment_close_and_reputation'],
    ('payment','disconnect'):['test_payment_transport_recovery.py::test_unknown_original_body_keeps_its_control_identity_under_a_new_parent'],
    ('payment','restart'):['test_payment_atomicity.py::test_concurrent_replay_restart_and_original_receipt',
                           'test_resident_payment_recovery.py::test_rejected_accepted_period_requires_new_confirmation_and_pays_once'],
    ('payment','lost'):['test_fulfillment_payment_guards.py::test_paid_lookup_that_loses_receipt_does_not_become_a_new_payment',
                        'test_payment_transport_recovery.py::test_existing_verified_payment_recovers_without_replaying_a_rejected_submission'],
    ('payment','unknown'):['test_fulfillment_payment_guards.py::test_unknown_payment_is_not_retried_or_forked',
                           'test_payment_transport_recovery.py::test_http_404_alone_and_missing_local_control_cannot_authorize_retry'],
    ('termination','disconnect'):['test_recovery_matrix.py::test_termination_survives_a_socket_drop_during_the_signed_notice'],
    ('termination','restart'):['test_resident_termination.py::test_service_settlement_recovers_original_context_and_committed_close'],
    ('termination','lost'):['test_resident_termination.py::test_lost_termination_ack_resumes_original_signed_notice_and_single_payment'],
    ('termination','unknown'):['test_resident_termination.py::test_termination_does_not_release_an_original_running_tool_or_hide_its_unknown_effect'],
}
STAGES=('authorize','contract','fulfillment','acceptance','payment','termination')
FAULTS=('disconnect','restart','lost','unknown')


def test_every_stage_and_fault_names_existing_recovery_tests():
    assert set(MATRIX)=={(stage,fault) for stage in STAGES for fault in FAULTS}
    for cell,nodes in MATRIX.items():
        assert nodes,cell
        for node in nodes:
            name,function=node.split('::')
            tree=ast.parse((TESTS/name).read_text(encoding='utf-8'))
            assert function in {item.name for item in tree.body if isinstance(item,ast.FunctionDef)},(cell,node)


def drop_once(monkeypatch,matches):
    """Close the live socket right after one matching original operation is saved; the outbox resends it."""
    original,dropped=AgentConnection.call,[]
    def call(self,actor,kind,payload,*,operation_id=None,timeout=10):
        if not dropped and matches(kind,payload):
            operation=self.submit(actor,kind,payload,operation_id)
            dropped.append(operation)
            if self._socket:self._socket.close()
            return self.wait(actor,kind,operation,timeout=max(timeout,20))
        return original(self,actor,kind,payload,operation_id=operation_id,timeout=timeout)
    monkeypatch.setattr(AgentConnection,'call',call)
    return dropped


def restart(runtime):
    config=runtime.config;runtime.close()
    fresh=ResidentRuntime(config,modules=(AssistantRole(),)).start()
    wait_for(lambda:fresh.connection.connected)
    return fresh


def test_authorization_waiting_for_the_user_survives_an_assistant_restart(platform,tmp_path):
    with participants(platform,tmp_path,fee=0) as (p,a,host,t,task):
        fresh=None
        try:
            local=LocalClient(a.config.home);category=platform.discovery.categories()[0]['id']
            prepared=local.call('assistant.delegation.prepare',{'purpose':'恢复矩阵授权','budget':'50','proposalFeeLimit':'1','categoryIds':[category]})
            assert prepared['status']=='completed',prepared
            args={'taskRef':prepared['data']['taskRef']};key=uid()
            pending=local.call('assistant.delegation.authorize',args,operation_id=key)
            assert pending['status']=='waiting' and platform.db.one('SELECT 1 FROM delegation_scopes WHERE scope_id=?',args['taskRef']) is None
            fresh=restart(a);local=LocalClient(fresh.config.home)
            assert local.call('assistant.delegation.authorize',args,operation_id=key)['data']['confirmationRef']==pending['data']['confirmationRef']
            user_decision(fresh,pending['data']['confirmationRef'])
            done=local.call('assistant.delegation.authorize',args,operation_id=key)
            assert done['status']=='completed' and done['data']['authorization']['state']=='active',done
            assert local.call('assistant.delegation.authorize',args,operation_id=key)['data']==done['data']
            assert len(platform.db.rows('SELECT * FROM delegation_grants WHERE scope=?',done['data']['scopeDigest']))==1
        finally:
            if fresh:fresh.close()


def test_contract_choice_waiting_for_the_user_survives_an_assistant_restart(platform,tmp_path):
    with participants(platform,tmp_path,formation='countersign',fee=0) as (p,a,host,t,task):
        fresh=None
        try:
            quote=candidate(a,p,t,task)
            pending,args,key=confirm(a,quote,approve=False);ref=pending['data']['contractRef']
            fresh=restart(a);local=LocalClient(fresh.config.home)
            user_decision(fresh,pending['data']['confirmationRef'])
            accepted=local.call('assistant.delegation.confirm',args,operation_id=key)
            assert accepted['status'] in ('accepted','completed'),accepted
            wait_for(lambda:status(fresh,ref) and status(fresh,ref)['status']=='completed')
            formed=status(fresh,ref)['data']
            assert formed['formed'] and formed['card']['signatures']['1']
            admission=formed['admissionId']
            assert len(platform.db.rows('SELECT * FROM delegation_contracts WHERE admission=?',admission))==1
        finally:
            if fresh:fresh.close()


def test_formation_survives_a_socket_drop_during_contract_binding(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,formation='countersign',fee=0) as (p,a,host,t,task):
        quote=candidate(a,p,t,task)
        dropped=drop_once(monkeypatch,lambda kind,payload:kind=='contract.bind')
        started,_,_=confirm(a,quote);ref=started['data']['contractRef']
        wait_for(lambda:status(a,ref) and status(a,ref)['status']=='completed')
        formed=status(a,ref)['data']
        assert len(dropped)==1 and formed['formed']
        assert len(platform.db.rows('SELECT * FROM delegation_contracts WHERE admission=?',formed['admissionId']))==1
        assert a.store.response(a.identities[0].agent_id,'contract.bind',dropped[0])['ok']


def test_termination_survives_a_socket_drop_during_the_signed_notice(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0) as (p,a,host,t,task):
        selected=candidate(a,p,t,task);formed,_,_=confirm(a,selected);ref=formed['data']['contractRef']
        wait_for(lambda:status(a,ref));local=LocalClient(a.config.home)
        def notice(kind,payload):
            return kind=='message.send' and payload.get('kind')=='a2c.request' and payload.get('body',{}).get('op')=='termination.notify'
        dropped=drop_once(monkeypatch,notice)
        args={'contractRef':ref,'mode':'unilateral'};key=uid()
        pending=local.call('assistant.delegation.terminate',args,operation_id=key)
        user_decision(a,pending['data']['confirmationRef'])
        local.call('assistant.delegation.terminate',args,operation_id=key)
        wait_for(lambda:(current:=operation(a,ref,key)) and current['status']=='completed',timeout=40)
        assert len(dropped)==1
        wait_for(lambda:platform.market.market.store.get('ledger',ref),timeout=40)
        client,_=a.modules[0].fulfillment.client(ref)
        assert client.card(ref)['fulfillment']['termination']
        notices=[row for row in platform.db.rows("SELECT envelope FROM delivery_messages WHERE kind='a2c.request' AND sender=?",a.identities[0].agent_id)
                 if '"termination.notify"' in (row['envelope'] or '')]
        assert len(notices)==1


def test_acceptance_survives_a_socket_drop_during_the_acceptance_notice(platform,tmp_path,monkeypatch):
    from test_resident_multiperiod import run_two_periods
    def acceptance(kind,payload):
        return kind=='message.send' and payload.get('kind')=='a2c.request' and payload.get('body',{}).get('op')=='acceptance.notify'
    dropped=drop_once(monkeypatch,acceptance)
    result=run_two_periods(platform,tmp_path,monkeypatch,'proposal','normal')
    assert len(dropped)==1 and result['scenario']=='normal'
