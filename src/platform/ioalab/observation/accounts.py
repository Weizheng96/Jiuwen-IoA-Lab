"""Account/browser read models; authorization is supplied by authenticated routes.

All queries read existing component facts and never create business state.
"""
from ..errors import LabError
from ..storage import jload
from .events import event_view
from .services import identity_rows


class AccountQueries:
    def __init__(self, db, *, auth, wallets, contracts, services, messages, bus, identities):
        self.db, self.auth, self.wallets = db, auth, wallets
        self.contracts, self.services, self.messages, self.bus = contracts, services, messages, bus
        self.identities = identities

    def exists(self, uid):
        return bool(self.db.one('SELECT 1 FROM users WHERE id=?', uid))

    def me(self, uid):
        account = self.db.one('SELECT id,username,kind,display_name,created,last_login FROM users WHERE id=?', uid)
        if not account:
            raise LabError('forbidden', '账号不可用', status=401)
        return {**account, 'roles': self.auth.roles(uid), 'wallet': str(self.wallets.balance(uid)),
                'agents': identity_rows(self.db, uid, ('id', 'role', 'name', 'status', 'created')),
                'pending': self.db.rows("SELECT * FROM confirmations WHERE owner_id=? AND state='pending' ORDER BY created", uid)}

    def sdk_me(self, uid):
        account = self.db.one('SELECT id,username,display_name,kind FROM users WHERE id=?', uid)
        if not account:
            raise LabError('forbidden', 'Account is unavailable', status=401)
        return {**account, 'wallet': str(self.wallets.balance(uid)), 'roles': self.auth.roles(uid),
                'identities': self.identities_of(uid)}

    def identities_of(self, uid):
        return self._listed(uid, self.identities.list_owned(uid))

    def services_of(self, uid):
        """Owner's provider metadata in its original HTTP shape, with registry listings applied."""
        return self._listed(uid, self.identities.services_owned(uid))

    def _listed(self, uid, identities):
        listed = {row['id']: row for row in identity_rows(self.db, uid, ('id', 'name', 'status'))}
        return [{**identity, 'name': listed[identity['id']]['name'], 'status': listed[identity['id']]['status']}
                for identity in identities]

    def admin_users(self):
        rows = self.db.rows('SELECT u.*,w.balance FROM users u LEFT JOIN wallets w ON w.user_id=u.id ORDER BY u.created')
        for row in rows:
            row.pop('pw_hash', None)
            row['agents'] = identity_rows(self.db, row['id'], ('id', 'role', 'name', 'status'))
            row['roles'] = self.auth.roles(row['id'])
        return rows

    def people(self):
        names = {}
        for agent in self.db.rows("SELECT owner_id,name FROM agents WHERE role='assistant' AND status<>'disabled' ORDER BY created,id"):
            names.setdefault(agent['owner_id'], []).append(agent['name'])
        return [{'username': user['username'], 'displayName': user['display_name'], 'assistantNames': names.get(user['id'], [])}
                for user in self.db.rows('SELECT id,username,display_name FROM users ORDER BY created,id')]

    def history(self, uid, username):
        peer = self.db.one('SELECT id FROM users WHERE username=?', username)
        if not peer:
            raise LabError('not_found', '用户不存在', status=404)
        return {'messages': [self.messages.view(row, private=True) for row in self.messages.rows(uid, peer=peer['id'])],
                'intents': self.contracts.intents(uid, peer=peer['id']),
                'contracts': [self.contracts.view(row, private=True) for row in self.contracts.rows(uid, peer=peer['id'])]}

    def statistics(self):
        return {'services': len(self.services.list()),
                'assistants': self.db.one("SELECT COUNT(*) AS n FROM agents WHERE role='assistant' AND status<>'disabled'")['n'],
                'interactions': self.db.one('SELECT COUNT(*) AS n FROM delivery_interactions')['n']}

    def needs(self, uid):
        return self.db.rows('SELECT id,text,state,card_digest,created,updated,llm_calls,tokens FROM needs WHERE owner_id=? ORDER BY created DESC', uid)

    def events(self, run='', after=0, limit=300):
        rows = self.db.rows('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',
                            run or self.bus.run_id, max(0, after), max(1, min(limit, 1000)))
        return self.render_events(rows)

    def runs(self):
        return sorted({self.bus.run_id, *[row['run_id'] for row in self.db.rows('SELECT DISTINCT run_id FROM events')]})

    def replay(self, run):
        rows = self.db.rows('SELECT * FROM events WHERE run_id=? ORDER BY seq', run)
        if not rows and run != self.bus.run_id:
            raise LabError('not_found', 'Not Found', status=404)
        return self.render_events(rows)

    @staticmethod
    def render_events(rows):
        for row in rows:
            row['detail'] = jload(row['detail'])
        return [event_view(row) for row in rows]
