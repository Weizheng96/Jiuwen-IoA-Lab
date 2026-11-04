"""Immutable, actually signed payment evidence; each component test owns a clone.

Only preparation is shared. No Runtime, Directory, database connection or callable
from the seed is retained. Public HTTP/SDK boundary tests keep their live fixture.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from types import MappingProxyType

import pytest

from conftest import free_port, wait_for


@dataclass(frozen=True)
class IdentityEvidence:
    payment: object


@dataclass(frozen=True)
class AccountEvidence:
    account: object


@dataclass(frozen=True)
class PayerEvidence:
    account: AccountEvidence
    identities: tuple


def fingerprint(directory):
    return tuple((p.relative_to(directory).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in sorted(directory.rglob('*')) if p.is_file())


@contextmanager
def original_core(directory):
    import uvicorn
    from ioalab.app import Platform
    from ioalab.config import Settings
    from ioalab.web.app import create_app
    port = free_port()
    core = Platform(Settings(bind='127.0.0.1', port=port, public_url_env=f'http://127.0.0.1:{port}',
                             data=directory, autoseed=True))
    server = uvicorn.Server(uvicorn.Config(create_app(core), host='127.0.0.1', port=core.s.port, log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: server.started)
        yield core
    finally:
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive(), 'Snapshot core still owns its resources'
        assert core._closed, 'Snapshot requires actual core shutdown'


@dataclass(frozen=True)
class PaymentSnapshot:
    settings: object
    directory: Path
    contents: tuple
    body_json: str
    reference: str
    payer: PayerEvidence
    provider: IdentityEvidence

    @contextmanager
    def clone(self, directory):
        from a2c.payment.ap2 import jwt_decode
        from ioalab.app import Platform
        body = json.loads(self.body_json)
        _, cm = jwt_decode(body['checkoutMandate'].rstrip('~'))
        _, checkout = jwt_decode(cm['checkout_jwt'])
        _, pm = jwt_decode(body['paymentMandate'].rstrip('~'))
        assert min(cm['exp'], checkout['exp'], pm['exp']) > time.time(), (
            'Original snapshot AP2 evidence expired; regenerate through the original signed flow')
        assert fingerprint(self.directory) == self.contents
        shutil.copytree(self.directory, directory)
        core = Platform(replace(self.settings, data=directory, autoseed=False))
        try:
            assert core.s.base_url == self.settings.base_url
            # Resolve real keys through this clone's persisted identity authority.
            # Directory.add is upstream's public trusted-key registration API.
            for party in (body['card']['client']['id'], body['template']['provider']):
                binding = core.identities.payment_party(party)
                core.keys.add(party, core.identities.enrollment.public_keys(binding['agentId']))
            assert core.psp.db is core.db and core.wallets.db is core.db
            assert core.psp.parties.__self__ is core.identities
            assert core.psp.client_matches.__self__ is core.identities
            assert not core.started and not core.background
            assert not core.db.one('SELECT 1 FROM payment_records WHERE ref=?', self.reference)
            yield core, (self.payer, self.reference, self.provider, body)
        finally:
            core.close()
            assert fingerprint(self.directory) == self.contents, 'A case mutated shared payment evidence'


def create_snapshot(root):
    from _resident_payment import signed_unpaid
    from ioalab.storage import DataOwner
    from ioalab_sdk.runtime.lease import Lease
    seed = root / 'seed-core'
    with original_core(seed) as core:
        settings = core.s
        with pytest.MonkeyPatch.context() as patch:
            with signed_unpaid(core, root / 'participants', patch) as (provider, assistant, reference, body):
                payer = PayerEvidence(AccountEvidence(MappingProxyType({'id': assistant.account.account['id']})),
                                      (IdentityEvidence(assistant.identities[0].payment),))
                service = IdentityEvidence(provider.identities[0].payment)
                body_json = json.dumps(body, ensure_ascii=False)
                sdk_homes = (provider.config.home, assistant.config.home)
            # The original context has closed both SDKs; verify actual OS leases.
            for home in sdk_homes:
                with Lease(home / 'client.lock', timeout=0):
                    pass
    snapshot = root / 'immutable-core'
    # Core shutdown completed before backup, copying or acquisition by a new owner.
    with DataOwner(seed):
        shutil.copytree(seed, snapshot, ignore=shutil.ignore_patterns(
            '.core.lock', 'ioalab.sqlite', 'ioalab.sqlite-wal', 'ioalab.sqlite-shm'))
        with sqlite3.connect((seed / 'ioalab.sqlite').as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot / 'ioalab.sqlite') as destination:
                source.backup(destination)
    return PaymentSnapshot(settings, snapshot, fingerprint(snapshot), body_json, reference, payer, service)
