"""Business request identity survives model call IDs; no external transport."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_tools.appointments import Appointments
from ioalab_tools.schedule import LocalSchedule
from test_assistant import appointment
from test_business_tools import context, schedule, uid


class Notification:
    def __init__(self):self.calls = []
    def send(self, *args):
        self.calls.append(args)
        return {'status': 'invite_submitted', 'uid': args[0], 'explicitTestDouble': True}


def test_completed_message_reuses_receipt_across_model_calls_restart_and_schedule_edit(tmp_path):
    calendar = schedule(tmp_path); notice = Notification()
    tool = Appointments(calendar, notice, 'owner@example.test')
    payload = appointment(); original = context()
    result = tool.book(payload, original)
    state = calendar.show(); document = state['document']
    document['events'] = [{'id': 'later-edit', **{k: payload[k] for k in ('title', 'start', 'end')}}]
    calendar.replace(document, state['revision'])
    restarted = Appointments(LocalSchedule(calendar.path), notice, 'owner@example.test')
    assert restarted.book(payload, {**original, 'operationId': uid()}) == result
    assert len(notice.calls) == 1 and list(restarted.records()) == [original['operationId']]
    assert restarted.records()[original['operationId']]['context'] == original
    with pytest.raises(RuntimeFault, match='input changed'):
        restarted.book({**payload, 'title': 'changed'}, {**original, 'operationId': uid()})
    with pytest.raises(RuntimeFault, match='input changed'):
        restarted.book(payload, {**original, 'messageId': uid()})
    assert len(notice.calls) == 1


def test_conflict_requires_sender_new_message_to_use_suggested_time(tmp_path):
    calendar = schedule(tmp_path); payload = appointment(); state = calendar.show()
    document = state['document']
    document['events'] = [{'id': 'busy', **{k: payload[k] for k in ('title', 'start', 'end')}}]
    calendar.replace(document, state['revision'])
    notice = Notification(); tool = Appointments(calendar, notice, 'owner@example.test')
    original = context(); conflict = tool.book(payload, original)
    assert conflict['status'] == 'conflict' and not notice.calls
    # A changed calendar cannot rewrite the completed decision for this message.
    state = calendar.show(); document = state['document']; document['events'] = []
    calendar.replace(document, state['revision'])
    assert tool.book(payload, {**original, 'operationId': uid()}) == conflict
    proposal = {**payload, 'start': conflict['suggestedStart'], 'end': conflict['suggestedEnd']}
    with pytest.raises(RuntimeFault, match='input changed'):
        tool.book(proposal, {**original, 'operationId': uid()})
    assert not notice.calls and len(tool.records()) == 1
    next_message = {**original, 'operationId': uid(), 'messageId': uid()}
    result = tool.book(proposal, next_message)
    assert result['status'] == 'invite_submitted' and result['calendar'] == 'awaiting_acceptance'
    assert len(notice.calls) == 1 and len(tool.records()) == 2
    assert tool.records()[next_message['operationId']]['context']['interactionId'] == original['interactionId']


def test_same_source_concurrent_model_calls_do_not_send_second_invitation(tmp_path):
    entered = threading.Event(); release = threading.Event()
    class BlockingNotification(Notification):
        def send(self, *args):
            self.calls.append(args); entered.set(); assert release.wait(10)
            return {'status': 'invite_submitted', 'uid': args[0], 'explicitTestDouble': True}
    calendar = schedule(tmp_path); notice = BlockingNotification(); payload = appointment(); original = context()
    first = Appointments(calendar, notice, 'owner@example.test')
    second = Appointments(LocalSchedule(calendar.path), notice, 'owner@example.test')
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.book, payload, original)
        try:
            assert entered.wait(3)
            with pytest.raises(RuntimeFault, match='automatic resend'):
                second.book(payload, {**original, 'operationId': uid()})
            assert len(notice.calls) == 1 and len(second.records()) == 1
        finally:release.set()
        result = pending.result(timeout=5)
    assert second.book(payload, {**original, 'operationId': uid()}) == result
    assert len(notice.calls) == 1


def test_unknown_message_stays_unknown_for_new_call_after_restart(tmp_path):
    class LostReceipt(Notification):
        def send(self, *args):
            self.calls.append(args); raise TimeoutError('explicit lost receipt')
    calendar = schedule(tmp_path); notice = LostReceipt(); payload = appointment(); original = context()
    with pytest.raises(RuntimeFault):
        Appointments(calendar, notice, 'owner@example.test').book(payload, original)
    restarted = Appointments(LocalSchedule(calendar.path), notice, 'owner@example.test')
    with pytest.raises(RuntimeFault, match='automatic resend'):
        restarted.book(payload, {**original, 'operationId': uid()})
    assert len(notice.calls) == 1 and len(restarted.records()) == 1
    assert restarted.records()[original['operationId']]['state'] == 'unknown'


def test_reserved_before_send_recovers_first_operation_and_invitation_identity(tmp_path, monkeypatch):
    calendar = schedule(tmp_path); notice = Notification(); original = context(); payload = appointment()
    first = Appointments(calendar, notice, 'owner@example.test'); save = first._save
    def interrupted(operation, record):
        if record['state'] == 'sending':raise RuntimeError('explicit crash before sending intent')
        save(operation, record)
    monkeypatch.setattr(first, '_save', interrupted)
    with pytest.raises(RuntimeError, match='explicit crash'):first.book(payload, original)
    record = first.records()[original['operationId']]
    assert record['state'] == 'reserved' and not notice.calls
    restarted = Appointments(LocalSchedule(calendar.path), notice, 'owner@example.test')
    receipt = restarted.book(payload, {**original, 'operationId': uid()})
    assert receipt['reservationId'] == record['reservationId'] and len(notice.calls) == 1
    assert notice.calls[0][0] == 'ioa-' + record['reservationId'] + '@jiuwen-ioa-lab'
    assert list(restarted.records()) == [original['operationId']]
    assert restarted.records()[original['operationId']]['context'] == original
