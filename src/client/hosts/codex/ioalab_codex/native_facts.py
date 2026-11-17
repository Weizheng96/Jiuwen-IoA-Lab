"""Private native machine correlation; absence is not identity conflict."""


class IdentityConflict(ValueError):
    pass


class NativeTurnFacts:
    def __init__(self, first_client, queue_client):
        self.clients = {'first': first_client, 'queue': queue_client}
        self.first_turn = None
        self.queue_turn = None
        self.queue_enabled = False
        self.client_turns = {}
        self.ends = {}
        self.seen = set()

    def bind_first(self, turn_id):
        if not isinstance(turn_id, str) or not turn_id:
            raise IdentityConflict('missing_actual_RPC_turn')
        if self.first_turn is not None and self.first_turn != turn_id:
            raise IdentityConflict('first_RPC_turn_changed')
        if not self.queue_enabled and any(value != turn_id for value in self.seen):
            raise IdentityConflict('early_turn_RPC_conflict')
        self.first_turn = turn_id

    def observe(self, turn, *, source):
        tid = turn.get('id')
        if not isinstance(tid, str) or not tid:
            raise IdentityConflict('missing_actual_turn_ID')
        self.seen.add(tid)
        if self.first_turn and tid != self.first_turn:
            if not self.queue_enabled:
                raise IdentityConflict('unexpected_turn_before_queue')
            if self.queue_turn not in (None, tid):
                raise IdentityConflict('multiple_queue_turns')
            self.queue_turn = tid  # Provisional identity, not queue causal proof.
        users = [i for i in turn.get('items', []) if i.get('type') == 'userMessage']
        if len(users) > 1:
            raise IdentityConflict('multiple_user_messages_in_turn')
        client_states = []
        for item in users:
            client = item.get('clientId')
            if client is None:
                client_states.append('null' if 'clientId' in item else 'absent')
                continue
            permitted = {'first': self.clients['first']}
            if self.queue_enabled:
                permitted['queue'] = self.clients['queue']
            phase = next((p for p, value in permitted.items() if value == client), None)
            if phase is None:
                raise IdentityConflict('unexpected_nonnull_client')
            if phase == 'first' and self.first_turn is not None and tid != self.first_turn:
                raise IdentityConflict('first_client_RPC_turn_conflict')
            if phase == 'queue' and tid == self.first_turn:
                raise IdentityConflict('queue_client_in_first_turn')
            if client in self.client_turns and self.client_turns[client] != tid:
                raise IdentityConflict('client_multiple_turns')
            self.client_turns[client] = tid
            client_states.append('expected:' + phase)
        if source == 'turn/completed':
            previous = self.ends.get(tid)
            if previous is not None and previous.get('status') != turn.get('status'):
                raise IdentityConflict('native_end_changed')
            self.ends[tid] = {k: turn.get(k) for k in ('id', 'status', 'completedAt')}
        return {'source': source, 'id': tid, 'status': turn.get('status'),
                'completedAtPresent': turn.get('completedAt') is not None,
                'turnKeys': sorted(turn), 'itemsView': turn.get('itemsView'),
                'itemTypes': [i.get('type') for i in turn.get('items', [])],
                'userKeys': [sorted(i) for i in users], 'clientStates': client_states}

    def terminal(self, client, turn):
        tid = turn.get('id')
        end = self.ends.get(tid)
        direct = client == self.clients['first'] and tid == self.first_turn and self.first_turn is not None
        if (not direct and self.client_turns.get(client) != tid) or not end:
            return False
        if end.get('status') != turn.get('status'):
            raise IdentityConflict('event_history_terminal_conflict')
        # Both same-writer completion event and concrete persisted end required.
        return turn.get('status') in ('completed', 'failed', 'interrupted') and turn.get('completedAt') is not None
