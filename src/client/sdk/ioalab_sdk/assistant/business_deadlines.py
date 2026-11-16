"""Original object deadlines and role-owned recovery of undecided confirmations.

Only this role layer understands contract objects; Confirmations is neutral.
Called after the role's real case/stage/operation checks, not from model input.
"""
from a2c import objects
from a2c.codec import parse_duration, parse_time
from a2c.jws import header_of
from a2c.slots import verify_card
from a2c.timing import accept_deadline
from a2c.payment.terms import verify_quote

from ..runtime import ConfirmationDeadline, RuntimeFault


def ensure_original(manager,binding,deadline,*,kind='approval',questions=None,recover=False):
    original=manager.find(binding,kind=kind,questions=questions)
    if original and 'responseDueAt' not in original:
        if recover and original['status'] in ('pending','expired'):
            try:
                return manager.migrate_pending(original['reference'],original['bindingDigest'],binding,deadline=deadline)
            except RuntimeFault as exc:
                if exc.code not in ('forbidden','expired'):raise
        return original
    return manager.ensure(binding,kind=kind,questions=questions,deadline=deadline)


def candidate_deadline(verified_candidate):
    # checked_candidate already verifies template, identity, scope and slots.
    return ConfirmationDeadline(expires_at=verified_candidate['until'].timestamp())


def fulfillment_deadline(client, record):
    card = client.card(record['args']['contractRef'])
    template = client.template(card['templateDigest'])
    chain = verify_card(template, card, client.keys)
    name = record['name']
    if name == 'terminate':
        if record['args']['mode']=='unilateral':
            return ConfirmationDeadline(object_bound=True)
        header=verify_quote(template,card,record['quote'],client.keys)
        until=parse_time(header['a2c-ts'])+parse_duration(template['deadlines']['respondWithin'])
        return ConfirmationDeadline(expires_at=until.timestamp())
    if name == 'answer_step':
        request = record['preview']['stepRequest']
        if objects.object_digest(request) != record['args']['stepRequestDigest']:
            raise RuntimeFault('id_conflict', 'Original step request changed')
        header = objects.verify_signed(
            request, client.keys.jwk(card['provider'], header_of(request['signature']).get('kid')),
            signer=card['provider'], expect_kid=True)
        due = parse_time(header['a2c-ts']) + parse_duration(template['deadlines']['authorizeWithin'])
    elif name == 'accept':
        due = accept_deadline(template, card, chain, record['args']['period'])
        if due is None:
            raise RuntimeFault('not_ready', 'Original delivered period is not anchored')
    elif name == 'pay':
        anchor = chain.period_ts(7, record['args']['period'])
        if anchor is None:
            raise RuntimeFault('forbidden', 'Payment needs the original accepted period')
        due = anchor + parse_duration(template['deadlines']['respondWithin'])
    else:
        raise RuntimeFault('invalid_input', 'This operation has no template response stage')
    # These protocol times establish lateness/arbitration grounds. They do not
    # invalidate a real user's late answer or consent to an unchanged live stage.
    return ConfirmationDeadline(response_due_at=due.timestamp())
